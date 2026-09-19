# -*- coding: utf-8 -*-
"""tcp_forward.py —— 极简 TCP 转发器：把连接固定转发到指定 IP。

用途单一：当"某个目标 IP 的 443 被中间设备丢掉"时（症状见 README.md），
把 git 的连接截到本地再固定转发到**另一个可达的 IP**。TLS 仍是端到端的
（SNI 与证书都由 git 与真 GitHub 完成），这里只换了目标 IP。

⚠ 关键：git 走 http 代理访问 https 时会先发 `CONNECT host:443`。
   转发器必须**自己回 `HTTP/1.1 200 Connection established` 再裸转发** ——
   原样把这行转给 GitHub，会被当成垃圾请求直接失败。

用法：
    python tcp_forward.py --listen 127.0.0.1:18443 --to 140.82.114.3:443

⚠ `--to` 请用 IP，别再走域名解析（否则又绕回被丢的那个 IP）。
"""
import argparse
import socket
import sys
import threading


def _utf8_stdout():
    """GBK 控制台下打印中文会 UnicodeEncodeError（本仓库的老坑）——入口一律兜住。"""
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(cli, target, verbose=False):
    cli.settimeout(15)
    try:
        head = cli.recv(65536)
    except OSError:
        head = b''
    cli.settimeout(None)
    if not head:
        cli.close()
        return

    is_connect = head[:7].upper() == b'CONNECT'
    try:
        srv = socket.create_connection(target, timeout=15)
    except OSError as e:
        if verbose:
            print('[forward] 连不上目标 %s: %s' % (target, e), flush=True)
        try:
            cli.close()
        except OSError:
            pass
        return

    if is_connect:
        cli.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
    else:
        srv.sendall(head)          # 非 CONNECT（明文请求）：把已读的头原样转过去
    if verbose:
        print('[forward] %s → %s' % ('CONNECT' if is_connect else 'plain', target), flush=True)

    threading.Thread(target=pipe, args=(cli, srv), daemon=True).start()
    threading.Thread(target=pipe, args=(srv, cli), daemon=True).start()


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(description='把本地端口的连接固定转发到某个 IP:port')
    ap.add_argument('--listen', default='127.0.0.1:18443', help='本地监听（默认 127.0.0.1:18443）')
    ap.add_argument('--to', required=True, help='转发目标 host:port（用 IP，别再解析域名）')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()

    lh, lp = a.listen.rsplit(':', 1)
    th, tp = a.to.rsplit(':', 1)
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind((lh, int(lp)))
    ls.listen(64)
    print('forward %s -> %s' % (a.listen, a.to), flush=True)
    while True:
        cli, _ = ls.accept()
        threading.Thread(target=handle, args=(cli, (th, int(tp)), a.verbose), daemon=True).start()


if __name__ == '__main__':
    main()
