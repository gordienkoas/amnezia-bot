import subprocess
import asyncio
import os

def generate_key():
    """Генерирует пару ключей WireGuard (приватный и публичный)."""
    private_key = subprocess.check_output(["wg", "genkey"]).decode().strip()
    public_key = subprocess.check_output(["wg", "pubkey"], input=private_key.encode()).decode().strip()
    return private_key, public_key

def allocate_ip():
    """Выделяет уникальный IP-адрес из подсети 10.0.0.0/24."""
    conf_path = "/root/amnezia-bot/awg/files/wg0.conf"
    used_ips = []
    
    try:
        with open(conf_path, "r") as f:
            for line in f:
                if "AllowedIPs" in line:
                    ip = line.split("=")[1].strip().split("/")[0]
                    used_ips.append(ip)
    except FileNotFoundError:
        pass
    
    for i in range(2, 255):  # Начинаем с 10.0.0.2
        ip = f"10.0.0.{i}"
        if ip not in used_ips:
            return ip
    raise Exception("Нет доступных IP-адресов.")

async def generate_vpn_key(conf_path: str) -> str:
    """Преобразует .conf в формат vpn:// с помощью awg-decode.py."""
    process = await asyncio.create_subprocess_exec(
        'python3.11', '/root/amnezia-bot/awg/awg-decode.py', '--encode', conf_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0 and stdout.decode().startswith('vpn://'):
        return stdout.decode().strip()
    else:
        print(f"Ошибка генерации vpn://: {stderr.decode()}")
        return ""

def add_peer(public_key, ip_address):
    """Добавляет новый пир в wg0.conf и синхронизирует с Docker."""
    conf_path = "/root/amnezia-bot/awg/files/wg0.conf"
    with open(conf_path, "a") as f:
        f.write(f"\n[Peer]\nPublicKey = {public_key}\nAllowedIPs = {ip_address}/32\n")
    subprocess.run(["docker", "exec", "amnezia-awg", "wg", "syncconf", "wg0", "/config/wg0.conf"])
