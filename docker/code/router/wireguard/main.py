from json import dumps
from pathlib import Path
from shutil import rmtree
from subprocess import check_call, check_output, run
from typing import Any, Iterator, Mapping, Tuple

from jinja2 import Environment
from std2.lex import split
from std2.pickle import encode
from std2.pickle.coders import ipv4_addr_encoder, ipv6_addr_encoder
from std2.types import IPNetwork

from ..consts import DATA, J2, SERVER_NAME, WG_IF, WG_PEERS_JSON, WG_PEERS
from ..ip import addr_show, link_show
from ..render import j2_build, j2_render
from ..subnets import load_networks
from ..types import DualStack, Networks, WGPeer

_SRV_TPL = Path("wg", "server.conf")
_CLIENT_TPL = Path("wg", "client.conf")


_WG_DATA = DATA / "wireguard"
_SRV_KEY = _WG_DATA / "keys" / "server" / "private.key"
_CLIENT_KEYS = _WG_DATA / "keys" / "clients"
_QR_DIR = _WG_DATA / "pub"


def _add_link() -> None:
    for link in link_show():
        if link.ifname == WG_IF:
            break
    else:
        check_call(("ip", "link", "add", WG_IF, "type", "wireguard"))


def _add_subnet(network: IPNetwork) -> None:
    for addr in addr_show():
        for info in addr.addr_info:
            if info.local in network and network.prefixlen == info.prefixlen:
                break
        else:
            address = f"{next(network.hosts())}/{network.prefixlen}"
            check_call(("ip", "address", "add", "dev", WG_IF, address))


def _set_up() -> None:
    check_call(("ip", "link", "set", "up", "dev", WG_IF))


def _srv_keys() -> Tuple[str, str]:
    _SRV_KEY.parent.mkdir(parents=True, exist_ok=True)
    if not _SRV_KEY.exists():
        pk = check_output(("wg", "genkey"), text=True)
        _SRV_KEY.write_text(pk)

    private_key = _SRV_KEY.read_text().rstrip()
    public_key = check_output(("wg", "pubkey"), input=private_key, text=True).rstrip()
    return private_key, public_key


def _client_keys() -> Iterator[Tuple[Path, str, str]]:
    _CLIENT_KEYS.mkdir(parents=True, exist_ok=True)

    for client in sorted(_CLIENT_KEYS.iterdir()):
        path = client.relative_to(_CLIENT_KEYS)

        private_key = client.read_text().rstrip()
        public_key = check_output(
            ("wg", "pubkey"), input=private_key, text=True
        ).rstrip()
        yield path, private_key, public_key


def _gen_client_keys() -> None:
    _CLIENT_KEYS.mkdir(parents=True, exist_ok=True)

    for peer in split(WG_PEERS):
        path = _CLIENT_KEYS / f"{peer}.key"
        if not path.exists():
            pk = check_output(("wg", "genkey"), text=True)
            path.write_text(pk)


def _wg_conf(j2: Environment, stack: DualStack) -> str:
    server_private, _ = _srv_keys()
    hosts = zip(stack.v4.hosts(), stack.v6.hosts())
    next(hosts)
    peers = (
        {
            "PUBLIC_KEY": peer_public,
            "V4_ADDR": f"{v4}/{stack.v4.max_prefixlen}",
            "V6_ADDR": f"{v6}/{stack.v6.max_prefixlen}",
        }
        for (_, _, peer_public), (v4, v6) in zip(_client_keys(), hosts)
    )
    env = {"SERVER_PRIVATE_KEY": server_private, "PEERS": peers}
    text = j2_render(j2, path=_SRV_TPL, env=env)
    return text


def _gen_qr(j2: Environment, networks: Networks) -> None:
    try:
        rmtree(_QR_DIR)
    except FileNotFoundError:
        pass
    _QR_DIR.mkdir(parents=True, exist_ok=True)
    WG_PEERS_JSON.parent.mkdir(parents=True, exist_ok=True)

    _, server_public = _srv_keys()
    stack = networks.wireguard
    hosts = zip(stack.v4.hosts(), stack.v6.hosts())
    dns_v4, dns_v6 = next(hosts)

    g_env = {
        "SERVER_PUBLIC_KEY": server_public,
        "DNS_ADDR_V4": dns_v4,
        "DNS_ADDR_V6": dns_v6,
        "WG_NETWORK_V4": stack.v4,
        "WG_NETWORK_V6": stack.v6,
        "TOR_NETWORK_V4": networks.tor.v4,
        "TOR_NETWORK_V6": networks.tor.v6,
        "LAN_NETWORK_V4": networks.lan.v4,
        "LAN_NETWORK_V6": networks.lan.v6,
        "GUEST_NETWORK_V4": networks.guest.v4,
        "GUEST_NETWORK_V6": networks.guest.v6,
        "SERVER_NAME": SERVER_NAME,
    }
    gen = tuple(zip(_client_keys(), hosts))
    data = {path.stem: WGPeer(v4=v4, v6=v6) for (path, _, _), (v4, v6) in gen}
    encoded = encode(data, encoders=(ipv4_addr_encoder, ipv6_addr_encoder))
    json = dumps(encoded)
    WG_PEERS_JSON.write_text(json)

    for (path, client_private, _), (v4, v6) in gen:
        v4_addr = f"{v4}/{stack.v4.max_prefixlen}"
        v6_addr = f"{v6}/{stack.v6.max_prefixlen}"

        conf_path = (_QR_DIR / path).with_suffix(".conf")
        qr_path = (_QR_DIR / path).with_suffix(".png")

        l_env: Mapping[str, Any] = {
            "CLIENT_PRIVATE_KEY": client_private,
            "CLIENT_ADDR_V4": v4_addr,
            "CLIENT_ADDR_V6": v6_addr,
        }
        env = {**l_env, **g_env}
        text = j2_render(j2, path=_CLIENT_TPL, env=env)
        conf_path.write_text(text)

        run(
            ("qrencode", "--output", str(qr_path)), input=text.encode()
        ).check_returncode()


def _wg_up(j2: Environment, stack: DualStack) -> None:
    conf = _wg_conf(j2, stack=stack)
    run(
        ("wg", "setconf", WG_IF, "/dev/stdin"), input=conf, text=True
    ).check_returncode()


def main() -> None:
    j2 = j2_build(J2)
    networks = load_networks()
    _gen_client_keys()
    _gen_qr(j2, networks=networks)
    _add_link()

    _add_subnet(networks.wireguard.v4)
    _add_subnet(networks.wireguard.v6)

    _wg_up(j2, stack=networks.wireguard)
    _set_up()