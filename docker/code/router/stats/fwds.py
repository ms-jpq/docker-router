from json import dumps
from subprocess import check_output

from std2.pickle import encode
from std2.pickle.coders import BUILTIN_ENCODERS

from ..consts import SHORT_DURATION
from ..port_fwd import forwarded_ports
from .subnets import load_networks


def feed() -> str:
    networks = load_networks()
    data = encode(tuple(forwarded_ports(networks)), encoders=BUILTIN_ENCODERS)
    json = dumps(data, check_circular=False, ensure_ascii=False)
    raw = check_output(("sortd", "yaml"), text=True, input=json, timeout=SHORT_DURATION)
    return raw.strip()
