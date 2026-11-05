# containment-demo (Theseus demo target)

A project with a hostile dependency (`lib/telemetry.js`). Its test tries to call
out to the network, write and read the host user's home, read SSH keys and API
keys, write a system folder and flood processes. `lib/hog.js` grabs memory until
it is stopped. Run `python3 theseus.py contain` to watch the lockbox hold.

Copyright (c) 2026 @Nefarious_Trey on X (https://x.com/Nefarious_Trey). MIT License.
