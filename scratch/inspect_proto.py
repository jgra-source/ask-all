import sqlite3
import base64

def parse_proto(data, depth=0):
    i = 0
    while i < len(data):
        key = 0; shift = 0
        while True:
            if i >= len(data): return
            b = data[i]; i += 1
            key |= (b & 0x7F) << shift
            if not (b & 0x80): break
            shift += 7
        tag = key >> 3; wire = key & 7
        if wire == 0:
            val = 0; shift = 0
            while True:
                b = data[i]; i += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80): break
                shift += 7
            print('  '*depth + f'{tag}: {val}')
        elif wire == 2:
            length = 0; shift = 0
            while True:
                b = data[i]; i += 1
                length |= (b & 0x7F) << shift
                if not (b & 0x80): break
                shift += 7
            val = data[i:i+length]; i += length
            try:
                s = val.decode('utf-8')
                if s.isprintable() and len(s) > 0:
                    print('  '*depth + f'{tag}: "{s}"')
                    continue
            except:
                pass
            print('  '*depth + f'{tag} (bytes, len={len(val)}):')
            parse_proto(val, depth+1)
        elif wire == 1:
            i += 8
            print('  '*depth + f'{tag}: 64-bit')
        elif wire == 5:
            i += 4
            print('  '*depth + f'{tag}: 32-bit')
        else:
            print('  '*depth + f'{tag}: unknown wire {wire}')
            break

conn = sqlite3.connect(r'C:\Users\John Anastacio\AppData\Roaming\Antigravity IDE\User\globalStorage\state.vscdb')
c = conn.cursor()
c.execute('SELECT value FROM ItemTable WHERE key = ?', ('antigravityUnifiedStateSync.userStatus',))
outer = base64.b64decode(c.fetchone()[0])

# Extract field 2 string
inner_b64 = outer.split(b'userStatusSentinelKey')[1]
# find start of base64
start = 0
while start < len(inner_b64) and inner_b64[start] < 32:
    start += 1
inner_raw = base64.b64decode(inner_b64[start:])
print("Inner raw length:", len(inner_raw))
parse_proto(inner_raw)
