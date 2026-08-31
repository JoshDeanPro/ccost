#!/usr/bin/env python3
"""Generate ccost pro license keys (fulfillment side)."""
import secrets, sys
A = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def make():
    payload = "".join(secrets.choice(A) for _ in range(15))
    body = "CCOST" + payload
    total = sum(A.index(c) * (i + 1) for i, c in enumerate(body))
    chk = A[total % 36] + A[(total // 36) % 36]
    return f"CCOST-{payload[:5]}-{payload[5:10]}-{payload[10:15]}-{chk}"

if __name__ == "__main__":
    for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 1):
        print(make())
