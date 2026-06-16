# Todo

## High Priority — Done
- [x] Debug `#companion-sysinfo-refresh` — add visual feedback and dedicated profile fetch
- [x] Fix `renderSysInfo()` field names to match actual backend data structure
- [x] Add backend fallback: `GET /profile` pulls `system_info` from other owners if current user has none
- [x] Track last active user in backend and save incoming sysinfo data to their profile

## Medium Priority — Pending
- [ ] Verify tab bar shows exactly 5 tabs (Check-in, Today, History, Profile, Routine)
- [ ] Verify System Info card collapses/expands, modal opens/closes correctly
