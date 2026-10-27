/**
 * src/net.js —— 局域网地址发现
 *
 * 服务监听了 0.0.0.0 只代表"愿意接受局域网连接"，但**用户得知道该访问哪个 IP**。
 * 这台机器上通常有一堆网卡，要挑出真正能用的那个：
 *
 *   · 跳过 127.0.0.1 —— loopback，只有本机能用
 *   · 跳过 169.254.x.x —— APIPA 自动私有地址，说明这块网卡**没拿到 DHCP**，连不通
 *   · 虚拟网卡（VMware / VirtualBox / Hyper-V / WSL 的 vEthernet / 蓝牙 / VPN）优先排到最后，
 *     因为它们也有 192.168.x.x，但不是真网卡，手机连不上
 */
import os from 'node:os';

/** 名字里带这些的，基本可以断定是虚拟/非常用网卡 */
const VIRTUAL_HINTS = [
  /vmware/i, /virtualbox/i, /vethernet/i, /hyper-?v/i, /wsl/i,
  /bluetooth/i, /蓝牙/i, /tap/i, /tun\b/i, /vpn/i, /zerotier/i, /tailscale/i,
  /loopback/i, /docker/i, /radmin/i,
];

const isVirtualName = (name) => VIRTUAL_HINTS.some((re) => re.test(name));

/**
 * 列出本机可用于局域网访问的 IPv4 地址。
 * @returns {Array<{name:string,address:string,netmask:string,cidr:string,virtual:boolean}>}
 */
export function lanAddresses() {
  const out = [];
  for (const [name, list] of Object.entries(os.networkInterfaces())) {
    for (const ni of list ?? []) {
      if (ni.family !== 'IPv4' || ni.internal) continue;      // 跳过 loopback
      if (ni.address.startsWith('169.254.')) continue;        // 跳过没拿到 DHCP 的网卡
      const prefix = maskToPrefix(ni.netmask);
      out.push({
        name,
        address: ni.address,
        netmask: ni.netmask,
        cidr: `${ni.address}/${prefix}`,
        network: networkOf(ni.address, prefix),
        virtual: isVirtualName(name),
      });
    }
  }
  // 真网卡排前面
  return out.sort((a, b) => Number(a.virtual) - Number(b.virtual));
}

/** 掩码 → 前缀长度，例如 255.255.255.0 → 24 */
export function maskToPrefix(mask) {
  if (!mask) return 24;
  const bits = mask.split('.').map(Number)
    .reduce((acc, n) => acc + ((n >>> 0).toString(2).match(/1/g)?.length ?? 0), 0);
  return bits;
}

/** 由 IP + 前缀算出网段，例如 192.168.1.14/24 → 192.168.1.0 */
export function networkOf(address, prefix) {
  const ip = address.split('.').map(Number);
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  const n = ((ip[0] << 24) | (ip[1] << 16) | (ip[2] << 8) | ip[3]) >>> 0;
  const net = (n & mask) >>> 0;
  return [net >>> 24, (net >>> 16) & 255, (net >>> 8) & 255, net & 255].join('.');
}

/** 组装访问地址信息（给启动横幅和 /api/net 共用） */
export function accessInfo({ host, port }) {
  const addresses = lanAddresses().map((a) => ({
    ...a,
    url: `http://${a.address}:${port}`,
    /** 同网段的设备才能直连（不同网段要么路由可达，要么就连不上） */
    sameSubnetHint: `手机/平板需连到 ${a.network}/${maskToPrefix(a.netmask)} 这个网段`,
  }));
  return {
    host,
    port,
    lanOpen: host === '0.0.0.0' || host === '::',
    localUrl: `http://localhost:${port}`,
    addresses,
    /** 首选地址：第一块真网卡 */
    primaryUrl: addresses.find((a) => !a.virtual)?.url ?? null,
  };
}
