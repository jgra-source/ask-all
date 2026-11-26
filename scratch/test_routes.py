with open(r'C:\Users\John Anastacio\AppData\Roaming\Claude\claude-code\2.1.330\9424831ad51c\claude.exe', 'rb') as f:
    content = f.read()
import re
matches = set(re.findall(rb'/api/oauth/organizations/[^\x00\r\n"\']+', content))
for m in sorted(matches):
    print(m.decode(errors='ignore'))
