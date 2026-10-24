-- factory_runner.lua — BizHawk (EmuHawk 2.11+) automation used by `factory run` / `factory play`.
--
-- EmuHawk is launched with:  --lua=<this file> --userdata=cfg:<run_config.lua> <rom>
-- run_config.lua returns a table written by tools/factory/emu.py (see that file for the schema).
--
-- Modes
--   capture : optional savestate load -> RAM hotpatches -> scripted inputs for N frames ->
--             screenshots / RAM watches / savestates -> result.json -> exit
--   record  : interactive play; logs every frame of input (for deterministic boot replays),
--             F11 = save a checkpoint state (+ screenshot), F10 = dump RAM (for factory ramsearch),
--             End = finish and exit
-- Everything is written under cfg.out_dir; result.json is written last (its presence = success).

local cfg_path = userdata.get("cfg")
local cfg = dofile(cfg_path)
local out = cfg.out_dir
local BUS = cfg.bus or "ARM9 System Bus"

-- ── tiny JSON encoder ──────────────────────────────────────────────────────────
local function json_str(s)
  return '"' .. tostring(s):gsub('[%c"\\]', function(c)
    local map = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
    return map[c] or string.format("\\u%04x", c:byte())
  end) .. '"'
end

local function is_array(t)
  local n = 0
  for k, _ in pairs(t) do
    if type(k) ~= "number" then return false end
    n = n + 1
  end
  return n == #t
end

local function json(v)
  local t = type(v)
  if t == "nil" then return "null"
  elseif t == "boolean" then return v and "true" or "false"
  elseif t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    if math.type and math.type(v) == "integer" then return tostring(v) end
    return string.format("%.6g", v)
  elseif t == "string" then return json_str(v)
  elseif t == "table" then
    local parts = {}
    if is_array(v) then
      for i = 1, #v do parts[#parts + 1] = json(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local items = {}
    for k, val in pairs(v) do items[#items + 1] = { tostring(k), val } end
    table.sort(items, function(a, b) return a[1] < b[1] end)
    for _, kv in ipairs(items) do parts[#parts + 1] = json_str(kv[1]) .. ":" .. json(kv[2]) end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return json_str(tostring(v))
end

-- ── files ─────────────────────────────────────────────────────────────────────
local log_f = io.open(out .. "/runner.log", "w")
local function log(msg)
  log_f:write(string.format("[%6d] %s\n", emu.framecount(), msg))
  log_f:flush()
end

local events_f = io.open(out .. "/events.jsonl", "w")
local function event(kind, data)
  data = data or {}
  data.kind = kind
  data.frame = cfg.frame_index or 0
  data.emu_frame = emu.framecount()
  events_f:write(json(data) .. "\n")
  events_f:flush()
end

local function write_file(path, text)
  local f = io.open(path, "w")
  f:write(text)
  f:close()
end

local function hex_to_bytes(h)
  local t = {}
  for byte in h:gmatch("%x%x") do t[#t + 1] = tonumber(byte, 16) end
  return t
end

local function bytes_to_hex(t)
  local s = {}
  for i = 1, #t do s[i] = string.format("%02X", t[i]) end
  return table.concat(s)
end

-- ── RAM hotpatching (savestates hold the ORIGINAL code; re-apply the mod after loading) ──
local hot = { applied = 0, already = 0, mismatch = 0, pending = 0, by_id = {} }

local function read_range(addr, len, domain)
  local t = {}
  for i = 0, len - 1 do t[#t + 1] = memory.read_u8(addr + i, domain) end
  return t
end

local function same(a, b)
  if #a ~= #b then return false end
  for i = 1, #a do if a[i] ~= b[i] then return false end end
  return true
end

local function apply_hotpatches(phase)
  if not cfg.hotpatch then return end
  for idx, p in ipairs(cfg.hotpatch) do
    local st = hot.by_id[idx]
    if st ~= "applied" and st ~= "already" then
      local domain = p.domain or BUS
      local orig = p.orig_bytes or hex_to_bytes(p.orig)
      local new = p.new_bytes or hex_to_bytes(p.new)
      p.orig_bytes, p.new_bytes = orig, new
      -- optional guard: the module must be resident (overlays share addresses)
      local resident = true
      if p.guard_addr then
        resident = same(read_range(p.guard_addr, #p.guard_bytes_t, domain), p.guard_bytes_t)
      end
      local cur = read_range(p.addr, #new, domain)
      if same(cur, new) then
        hot.by_id[idx] = "already"; hot.already = hot.already + 1
      elseif resident and same(cur, orig) then
        for i = 1, #new do memory.write_u8(p.addr + i - 1, new[i], domain) end
        hot.by_id[idx] = "applied"; hot.applied = hot.applied + 1
        event("hotpatch", { id = idx, addr = string.format("0x%08X", p.addr), module = p.module, phase = phase })
      else
        if st ~= "mismatch" and not p.optional then
          event("hotpatch_mismatch", { id = idx, addr = string.format("0x%08X", p.addr), module = p.module,
                                       found = bytes_to_hex(cur), phase = phase })
        end
        hot.by_id[idx] = "mismatch"
      end
    end
  end
end

local function prepare_hotpatches()
  if not cfg.hotpatch then return end
  for _, p in ipairs(cfg.hotpatch) do
    if p.guard then
      p.guard_addr = p.guard.addr
      p.guard_bytes_t = hex_to_bytes(p.guard.bytes)
    end
  end
end

-- ── inputs ────────────────────────────────────────────────────────────────────
local schedule = {}      -- frame -> { buttons = {A=true}, touch = {x, y} }

local function add_input(frame, buttons, touch)
  local e = schedule[frame] or { buttons = {} }
  for _, b in ipairs(buttons or {}) do e.buttons[b] = true end
  if touch then e.touch = touch end
  schedule[frame] = e
end

local function load_schedule()
  for _, inp in ipairs(cfg.inputs or {}) do
    local hold = inp.hold or 1
    for k = 0, hold - 1 do add_input(inp.frame + k, inp.buttons, inp.touch) end
  end
  if cfg.input_log then
    local f = io.open(cfg.input_log, "r")
    if not f then error("input_log não encontrado: " .. cfg.input_log) end
    for line in f:lines() do
      if not line:match("^%s*#") and line:match("%S") then
        local fr, btns, touch = line:match("^(%d+)%s*([^%s]*)%s*(.*)$")
        local buttons = {}
        for b in (btns or ""):gmatch("[^,]+") do if b ~= "-" then buttons[#buttons + 1] = b end end
        local tx, ty = (touch or ""):match("T:(%d+),(%d+)")
        add_input(tonumber(fr) + (cfg.input_log_offset or 0), buttons,
                  tx and { tonumber(tx), tonumber(ty) } or nil)
      end
    end
    f:close()
  end
end

local function apply_inputs(frame)
  local e = schedule[frame]
  if not e then return end
  joypad.set(e.buttons)
  if e.touch then
    joypad.set({ Touch = true })
    joypad.setanalog({ ["Touch X"] = e.touch[1], ["Touch Y"] = e.touch[2] })
  end
end

-- ── RAM watches ───────────────────────────────────────────────────────────────
local readers = {
  u8 = memory.read_u8, s8 = memory.read_s8, u16 = memory.read_u16_le, s16 = memory.read_s16_le,
  u32 = memory.read_u32_le, s32 = memory.read_s32_le,
}
local watch_f
local last_watch = {}

local function open_watches()
  if not cfg.watches or #cfg.watches == 0 then return end
  watch_f = io.open(out .. "/ram.csv", "w")
  local cols = { "frame" }
  for _, w in ipairs(cfg.watches) do cols[#cols + 1] = w.name end
  watch_f:write(table.concat(cols, ",") .. "\n")
end

local function sample_watches(frame)
  if not watch_f then return end
  local row = { tostring(frame) }
  for _, w in ipairs(cfg.watches) do
    local fn = readers[w.type or "u32"] or memory.read_u32_le
    local ok, v = pcall(fn, w.addr, w.domain or BUS)
    v = ok and v or "ERR"
    row[#row + 1] = tostring(v)
    last_watch[w.name] = v
  end
  watch_f:write(table.concat(row, ",") .. "\n")
end

-- ── capture helpers ───────────────────────────────────────────────────────────
local capture_set = {}
for _, f in ipairs((cfg.capture and cfg.capture.frames) or {}) do capture_set[f] = true end
local captured = 0

local function shot(frame, name)
  local path = string.format("%s/frames/%s.png", out, name or string.format("f%06d", frame))
  client.screenshot(path)
  captured = captured + 1
end

local state_at = {}
for _, s in ipairs(cfg.save_states or {}) do state_at[s.frame] = s.path end

-- full RAM snapshots for offline "RAM search" (factory ramsearch); 4 MiB Main RAM takes ~15 ms
local dump_at = {}
for _, f in ipairs(cfg.ram_dumps or {}) do dump_at[f] = true end
local DUMP_DOMAINS = cfg.ram_dump_domains or { "Main RAM" }

local function dump_ram(tag)
  for _, d in ipairs(DUMP_DOMAINS) do
    local size = memory.getmemorydomainsize(d)
    local data = memory.read_bytes_as_binary_string(0, size, d)
    local name = d:gsub("%s+", "_")
    local f = io.open(string.format("%s/ram/%s_%s.bin", out, name, tag), "wb")
    f:write(data)
    f:close()
  end
  event("ram_dump", { tag = tag })
end

-- ── modes ─────────────────────────────────────────────────────────────────────
local function finish(result)
  if watch_f then watch_f:close() end
  result.hotpatch = { applied = hot.applied, already = hot.already,
                      mismatch = 0, total = cfg.hotpatch and #cfg.hotpatch or 0, status = hot.by_id }
  for _, st in pairs(hot.by_id) do if st == "mismatch" then result.hotpatch.mismatch = result.hotpatch.mismatch + 1 end end
  result.last_watch = last_watch
  result.captured = captured
  result.emu_framecount = emu.framecount()
  result.lagcount = emu.lagcount()
  write_file(out .. "/result.json", json(result))
  log("fim: " .. json(result))
  log_f:close()
  events_f:close()
  if cfg.exit ~= false then client.exit() end
end

local function capture_mode()
  local t0 = os.clock()
  local res = { ok = true, mode = "capture", frames = cfg.frames }
  if cfg.state then
    local ok = savestate.load(cfg.state, true)
    event("state_load", { path = cfg.state, ok = ok })
    if not ok then error("falha ao carregar savestate: " .. cfg.state) end
  end
  prepare_hotpatches()
  apply_hotpatches("start")
  load_schedule()
  open_watches()
  sample_watches(0)
  if capture_set[0] or (cfg.capture and cfg.capture.first) then shot(0) end
  if dump_at[0] then dump_ram(string.format("f%06d", 0)) end
  local every = (cfg.capture and cfg.capture.every) or 0
  local all = cfg.capture and cfg.capture.all
  local watch_every = cfg.watch_every or 1
  local hot_every = cfg.hotpatch_every or 30
  for frame = 1, cfg.frames do
    cfg.frame_index = frame
    apply_inputs(frame)
    emu.frameadvance()
    if cfg.hotpatch and frame % hot_every == 0 then apply_hotpatches("periodic") end
    if all or capture_set[frame] or (every > 0 and frame % every == 0) then shot(frame) end
    if frame % watch_every == 0 then sample_watches(frame) end
    if dump_at[frame] then dump_ram(string.format("f%06d", frame)) end
    if state_at[frame] then
      savestate.save(state_at[frame], true)
      event("state_save", { path = state_at[frame] })
    end
  end
  if cfg.final_state then
    savestate.save(cfg.final_state, true)
    event("state_save", { path = cfg.final_state, final = true })
  end
  res.seconds = os.clock() - t0
  res.fps = res.seconds > 0 and cfg.frames / res.seconds or nil
  finish(res)
end

local function record_mode()
  -- interactive: the human plays; we log inputs every frame and react to F11 / End
  local res = { ok = true, mode = "record" }
  if cfg.state then
    savestate.load(cfg.state, true)
    event("state_load", { path = cfg.state })
  end
  prepare_hotpatches()
  apply_hotpatches("start")
  local inp_f = io.open(out .. "/inputs.txt", "w")
  inp_f:write("# frame\tbuttons\ttouch   (gravado por factory play; frame 1 = primeiro frame após o início)\n")
  local order = { "A", "B", "X", "Y", "L", "R", "Start", "Select", "Up", "Down", "Left", "Right", "LidClose", "LidOpen" }
  local prev = {}
  local frame = 0
  local checkpoints = 0
  local dumps = 0
  while true do
    emu.frameadvance()
    frame = frame + 1
    cfg.frame_index = frame
    if cfg.hotpatch and frame % (cfg.hotpatch_every or 30) == 0 then apply_hotpatches("periodic") end
    local pad = joypad.get()
    local pressed = {}
    for _, b in ipairs(order) do if pad[b] then pressed[#pressed + 1] = b end end
    local touch = ""
    if pad["Touch"] then touch = string.format("T:%d,%d", pad["Touch X"] or 0, pad["Touch Y"] or 0) end
    if #pressed > 0 or touch ~= "" then
      inp_f:write(string.format("%d\t%s\t%s\n", frame, #pressed > 0 and table.concat(pressed, ",") or "-", touch))
      inp_f:flush()
    end
    local keys = input.get()
    if keys["F11"] and not prev["F11"] then
      checkpoints = checkpoints + 1
      local name = string.format("checkpoint_%02d_f%06d", checkpoints, frame)
      savestate.save(out .. "/" .. name .. ".State", true)
      client.screenshot(out .. "/" .. name .. ".png")
      event("checkpoint", { name = name, record_frame = frame })
      if gui and gui.addmessage then gui.addmessage("factory: checkpoint salvo (" .. name .. ")") end
    end
    if keys["F10"] and not prev["F10"] then
      dumps = dumps + 1
      dump_ram(string.format("mark%02d_f%06d", dumps, frame))
      client.screenshot(string.format("%s/ram/mark%02d_f%06d.png", out, dumps, frame))
      if gui and gui.addmessage then gui.addmessage("factory: RAM salva (marca " .. dumps .. ")") end
    end
    if (keys["End"] and not prev["End"]) or (cfg.max_frames and frame >= cfg.max_frames) then
      event("finish", { record_frame = frame })
      break
    end
    prev = keys
  end
  inp_f:close()
  res.frames = frame
  res.checkpoints = checkpoints
  finish(res)
end

local ok, err = pcall(function()
  client.SetSoundOn(cfg.sound == true)
  if cfg.mode ~= "record" then
    emu.limitframerate(false)
    client.speedmode(cfg.speed or 6400)
  end
  log("início: modo=" .. tostring(cfg.mode) .. " rom=" .. tostring(gameinfo and gameinfo.getromname and gameinfo.getromname() or "?"))
  if cfg.mode == "record" then record_mode() else capture_mode() end
end)

if not ok then
  local res = { ok = false, error = tostring(err), mode = cfg.mode }
  pcall(function() log("ERRO: " .. tostring(err)) end)
  pcall(finish, res)
  if cfg.exit ~= false then client.exit() end
end
