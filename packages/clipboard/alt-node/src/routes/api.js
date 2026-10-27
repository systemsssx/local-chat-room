/**
 * src/routes/api.js —— 普通 HTTP 接口：快照 / 发消息 / 历史 / 上传
 *
 * 三种传输共用这一套：
 *   · 轮询：下行靠 GET /api/snapshot，上行 POST /api/messages
 *   · SSE ：下行靠 /api/sse，上行**仍然是** POST /api/messages
 *   · WS  ：上下行都走 socket，不走这里
 */
import multer from 'multer';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { config } from '../config.js';
import { roomStore, hub, presence } from '../rooms.js';

/* ---------- 校验与类型白名单 ---------- */
export const validRoom = (v) => /^[A-Za-z0-9_-]{1,32}$/.test(String(v ?? ''));

const IMAGE_MIME = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);
const IMAGE_EXT = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp']);
const TEXT_EXT = new Set(['.txt', '.md', '.py', '.js', '.mjs', '.json', '.css', '.html', '.csv',
  '.log', '.yml', '.yaml', '.ini', '.conf', '.sh', '.c', '.cpp', '.java', '.go', '.rs', '.sql', '.ts']);

export const isImageFile = (mime, ext) => IMAGE_MIME.has(mime) || IMAGE_EXT.has(ext);
export const isTextFile = (mime, ext) => String(mime).startsWith('text/') || TEXT_EXT.has(ext);

/* ---------- 两个中文/编码相关的坑，修在这里 ---------- */

/**
 * 坑一：multipart 里的文件名默认被按 latin1 解出来，中文名会变成「çé¢æªå¾.png」。
 * 先按 latin1 还原成字节、再按 UTF-8 解一次；解出替换字符说明本来就是对的，保持原样。
 */
export function fixFilename(name) {
  const decoded = Buffer.from(String(name), 'latin1').toString('utf8');
  return decoded.includes('\uFFFD') ? name : decoded;
}

/**
 * 坑二：Windows 记事本存的 .txt 默认是 ANSI/GBK，直接按 UTF-8 读会乱码。
 * 反过来先按 UTF-8 严格解，失败再按 GB18030 解 —— Node 自带完整 ICU，**不需要 iconv-lite**。
 */
export function decodeText(buf) {
  try {
    return { text: new TextDecoder('utf-8', { fatal: true }).decode(buf), encoding: 'UTF-8' };
  } catch {
    try {
      return { text: new TextDecoder('gb18030').decode(buf), encoding: 'GB18030' };
    } catch {
      return { text: buf.toString('latin1'), encoding: 'Latin-1' };
    }
  }
}

/* ---------- 上传 ---------- */
const upload = multer({
  dest: config.uploadDir,
  limits: { fileSize: config.maxUploadBytes, files: 1 },
  fileFilter(req, file, cb) {
    const ext = path.extname(file.originalname).toLowerCase();
    // 有些客户端不给 multipart 部分带 Content-Type，所以扩展名也要兜底
    if (!isImageFile(file.mimetype, ext) && !isTextFile(file.mimetype, ext)) {
      return cb(new Error('只接受图片或纯文本文件'));
    }
    cb(null, true);
  },
});

/* ---------- 路由 ---------- */
export function mountApiRoutes(app) {
  fs.mkdirSync(config.uploadDir, { recursive: true });

  /** 房间快照：初始加载 / 切换传输补差额 / 轮询下行，三处共用 */
  app.get('/api/snapshot/:room', (req, res) => {
    const { room } = req.params;
    if (!validRoom(room)) return res.status(400).json({ error: '房间号不合法' });

    const since = Number(req.query.since) || 0;
    presence.touch(req, room, {
      clientId: req.query.clientId,
      nickname: req.query.nickname,
      transport: req.query.transport || 'polling',
    });
    res.json({
      room,
      seq: roomStore.seq,
      messages: roomStore.since(room, since),
      members: presence.list(room),
      serverTime: Date.now(),
    });
  });

  /** 发消息（轮询版与 SSE 版的上行通道） */
  app.post('/api/messages/:room', (req, res) => {
    const { room } = req.params;
    if (!validRoom(room)) return res.status(400).json({ error: '房间号不合法' });

    const body = req.body ?? {};
    const text = typeof body.text === 'string' ? body.text : '';
    if (Buffer.byteLength(text, 'utf8') > config.maxTextBytes) {
      return res.status(413).json({ error: `单条内容超过 ${Math.round(config.maxTextBytes / 1024)}KB` });
    }

    const client = presence.touch(req, room, body);
    const msg = roomStore.push(room, body, client);
    hub.broadcastMessages(room, [msg]);
    hub.broadcastMembers(room, presence.list(room));
    res.json({ ok: true, message: msg });
  });

  /** 历史记录（下一步换成 SQLite 查询，返回结构不变） */
  app.get('/api/history/:room', (req, res) => {
    const { room } = req.params;
    if (!validRoom(room)) return res.status(400).json({ error: '房间号不合法' });

    const limit = Math.min(Number(req.query.limit) || config.historyLimit, 50);
    const items = roomStore.history(room, limit).map((m) => ({
      seq: m.seq,
      at: m.at,
      type: m.type,
      text: m.text ?? '',
      label: (m.text || m.file?.name || '(空)').slice(0, 40),
    }));
    res.json({ room, items });
  });

  /** 上传图片或纯文本文件：返回 file 描述对象，再由前端塞进消息里发出去 */
  app.post('/api/upload', (req, res) => {
    upload.single('file')(req, res, (err) => {
      if (err) {
        const tooBig = err.code === 'LIMIT_FILE_SIZE';
        return res.status(tooBig ? 413 : 400).json({
          error: tooBig ? `文件超过 ${Math.round(config.maxUploadBytes / 1024 / 1024)}MB` : String(err.message ?? err),
        });
      }
      const f = req.file;
      if (!f) return res.status(400).json({ error: '没有收到文件' });

      const originalName = fixFilename(f.originalname);
      const ext = path.extname(originalName).toLowerCase() || '';
      const id = randomUUID().replace(/-/g, '').slice(0, 12);
      const storedName = id + ext;
      fs.renameSync(f.path, path.join(config.uploadDir, storedName));

      const kind = isImageFile(f.mimetype, ext) ? 'image' : 'file';
      const file = {
        id,
        url: '/uploads/' + storedName,
        name: originalName,
        size: f.size,
        mime: f.mimetype,
        kind,
      };

      // 纯文本文件：顺手解码并给一段预览（GBK/UTF-8 自动识别）
      if (kind === 'file') {
        const buf = fs.readFileSync(path.join(config.uploadDir, storedName));
        const { text, encoding } = decodeText(buf.subarray(0, 256 * 1024));
        const lines = text.split(/\r?\n/);
        file.encoding = encoding;
        file.lines = lines.length;
        file.preview = lines.slice(0, 6).join('\n').slice(0, 600);
      }

      console.log(`[upload] ${originalName} → ${storedName} (${f.size}B, ${kind})`);
      res.json({ ok: true, file });
    });
  });
}
