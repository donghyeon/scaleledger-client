import re
import socket
import uuid


def get_mac_address():
    mac = uuid.getnode()
    return ":".join(re.findall("..", "%012x" % mac))


def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_hostname():
    return socket.gethostname()
