#!/usr/bin/env bash
# Make the monitor switch to the Pi's HDMI input.
#
#   hdmi-source.sh status    show what the script can see (CEC, DDC/CI)
#   hdmi-source.sh claim     try once, exit 0 if it worked
#   hdmi-source.sh boot      keep trying for CLAIM_SECONDS while the monitor starts up
#   hdmi-source.sh watch     switch back to the Pi each time the monitor wakes up
#   hdmi-source.sh service   boot, then watch (this is what hdmi-source.service runs)
#
# Settings come from /etc/default/caldisplay. Needs root.

set -u

CEC_ENABLE=auto
CEC_DEVICE=/dev/cec0
OSD_NAME=Calendar
DDC_INPUT=
DDC_BUS=
CLAIM_SECONDS=60
WATCH=yes
WATCH_INTERVAL=10
# shellcheck disable=SC1091
[ -r /etc/default/caldisplay ] && . /etc/default/caldisplay

log() { echo "hdmi-source: $*"; }

# ---------------------------------------------------------------- HDMI-CEC

have_cec() {
  [ "$CEC_ENABLE" != no ] && [ -e "$CEC_DEVICE" ] && command -v cec-ctl >/dev/null 2>&1
}

cec() { cec-ctl -d "$CEC_DEVICE" "$@" 2>/dev/null; }

cec_phys() {  # our HDMI physical address, like 1.0.0.0 (f.f.f.f = no link)
  cec | awk -F': *' '/Physical Address/ { gsub(/ /, "", $2); print $2; exit }'
}

cec_power() {  # monitor power state: on, standby, to-on, to-standby, or empty
  cec --to 0 --give-device-power-status | awk '/pwr-state/ { print $2; exit }'
}

cec_claim() {
  have_cec || return 1
  cec --playback --osd-name "$OSD_NAME" >/dev/null || { log "CEC: could not configure $CEC_DEVICE"; return 1; }
  local pa
  pa="$(cec_phys)"
  if [ -z "$pa" ] || [ "$pa" = "f.f.f.f" ]; then
    log "CEC: no HDMI link yet"
    return 1
  fi
  cec --to 0 --image-view-on >/dev/null
  if cec --to 15 --active-source phys-addr="$pa" >/dev/null; then
    log "CEC: asked the monitor to switch to $pa"
    return 0
  fi
  log "CEC: monitor did not accept the message"
  return 1
}

# ------------------------------------------------------------------ DDC/CI

have_ddc() { [ -n "$DDC_INPUT" ] && command -v ddcutil >/dev/null 2>&1; }

ddc() {
  if [ -n "$DDC_BUS" ]; then ddcutil --bus "$DDC_BUS" "$@" 2>/dev/null
  else ddcutil "$@" 2>/dev/null; fi
}

hexnorm() {  # 0x12, x12, 12, X0012 -> 12 (last byte, lowercase)
  local v="${1,,}"
  v="${v#0x}"; v="${v#x}"
  [ -n "$v" ] || return 1
  printf '%02x' $(( 16#$v & 0xff ))
}

ddc_current() {  # current input code, empty if the monitor isn't answering
  local v
  v="$(ddc getvcp 60 --brief | awk '/^VCP 60/ { print $4; exit }')"
  [ -n "$v" ] && hexnorm "$v"
}

# returns 0 = on the Pi's input, 2 = switch sent but not confirmed, 1 = failed
ddc_claim() {
  have_ddc || return 1
  local want cur
  want="$(hexnorm "$DDC_INPUT")" || { log "DDC: bad DDC_INPUT '$DDC_INPUT'"; return 1; }
  cur="$(ddc_current)"
  if [ "$cur" = "$want" ]; then
    log "DDC: monitor is on the Pi's input (0x$want)"
    return 0
  fi
  if ddc --noverify setvcp 60 "0x$want" >/dev/null; then
    sleep 2
    if [ "$(ddc_current)" = "$want" ]; then
      log "DDC: switched monitor to input 0x$want"
      return 0
    fi
    log "DDC: sent input 0x$want (not confirmed yet)"
    return 2
  fi
  [ -z "$cur" ] && log "DDC: monitor not answering" || log "DDC: switch to 0x$want failed"
  return 1
}

# ---------------------------------------------------------------- commands

nothing_configured() {
  ! have_cec && ! have_ddc
}

do_claim() {
  local ok=1
  cec_claim && ok=0
  ddc_claim; [ $? -ne 1 ] && ok=0
  return $ok
}

do_boot() {
  if nothing_configured; then
    log "No CEC device and no DDC_INPUT set. Nothing to do (see /etc/default/caldisplay)."
    return 0
  fi
  local end=$(( SECONDS + CLAIM_SECONDS )) cec_sent=0 rc
  while [ "$SECONDS" -lt "$end" ]; do
    if have_ddc; then
      ddc_claim; rc=$?
      [ $rc -eq 0 ] && return 0
    fi
    if cec_claim; then
      cec_sent=$(( cec_sent + 1 ))
      # Repeat a few times in case the monitor was still booting.
      [ "$cec_sent" -ge 3 ] && ! have_ddc && return 0
    fi
    sleep 5
  done
  [ "$cec_sent" -gt 0 ] && return 0
  log "Gave up after ${CLAIM_SECONDS}s"
  return 1
}

monitor_awake() {
  if have_cec; then
    case "$(cec_power)" in on|to-on) return 0 ;; esac
  fi
  if have_ddc; then
    [ -n "$(ddc_current)" ] && return 0
  fi
  return 1
}

do_watch() {
  [ "$WATCH" = yes ] || return 0
  nothing_configured && return 0
  log "Watching for the monitor to wake up"
  local was=awake now
  while sleep "$WATCH_INTERVAL"; do
    if monitor_awake; then now=awake; else now=asleep; fi
    if [ "$was" = asleep ] && [ "$now" = awake ]; then
      log "Monitor woke up, switching it to the Pi"
      do_boot
    fi
    was=$now
  done
}

do_status() {
  echo "CEC:"
  if have_cec; then
    echo "  device:           $CEC_DEVICE"
    echo "  physical address: $(cec_phys)"
    echo "  monitor power:    $(cec_power || true)"
  elif [ -e "$CEC_DEVICE" ]; then
    echo "  $CEC_DEVICE exists but cec-ctl is missing (apt install v4l-utils)"
  else
    echo "  no $CEC_DEVICE on this board/kernel"
  fi
  echo "DDC/CI:"
  if ! command -v ddcutil >/dev/null 2>&1; then
    echo "  ddcutil not installed"
  elif [ -z "$DDC_INPUT" ]; then
    echo "  DDC_INPUT not set in /etc/default/caldisplay"
    echo "  find it with: sudo ddcutil capabilities | grep -A8 'Feature: 60'"
  else
    local cur
    cur="$(ddc_current)"
    echo "  wanted input:     0x$(hexnorm "$DDC_INPUT")"
    echo "  current input:    ${cur:+0x$cur}${cur:-monitor not answering}"
  fi
}

[ "$(id -u)" = 0 ] || { echo "Run as root: sudo $0 ${1:-}"; exit 1; }

case "${1:-}" in
  claim)   do_claim ;;
  boot)    do_boot ;;
  watch)   do_watch ;;
  service) do_boot; do_watch ;;
  status)  do_status ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
