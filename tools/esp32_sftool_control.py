"""Control/log the ESP SFTool transport on CH343; native USB is reserved for SFTool."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import serial
from serial.tools import list_ports


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=int,default=180)
    p.add_argument('--reset-esp',action='store_true')
    g=p.add_mutually_exclusive_group()
    g.add_argument('--probe',action='store_true')
    g.add_argument('--arm-bridge',action='store_true')
    a=p.parse_args()
    if not 1<=a.seconds<=300:p.error('Duration must be 1..300 seconds')
    port=next((x for x in list_ports.comports() if x.device=='COM8'),None)
    if not port or (port.vid,port.pid)!=(0x1A86,0x55D3):raise RuntimeError('CH343 COM8 absent')
    a.output.mkdir(parents=True,exist_ok=False)
    state={'started_utc':datetime.now(timezone.utc).isoformat(),'action':'probe' if a.probe else 'bridge' if a.arm_bridge else 'disarm',
           'identity_verified':False,'armed':False,'probe_done':False,'complete_stream':False}
    pc=serial.Serial(port=None,baudrate=921600,timeout=.2,write_timeout=2)
    pc.dtr=False;pc.rts=False;pc.port='COM8';pending=bytearray()
    last_arm_time=None;wait_baseline=None
    try:
        pc.open()
        if a.reset_esp:pc.rts=True;time.sleep(.1);pc.rts=False
        deadline=time.monotonic()+a.seconds;identity_deadline=time.monotonic()+10;next_info=time.monotonic()+.5
        with (a.output/'events.jsonl').open('w',encoding='utf-8') as log:
            while time.monotonic()<deadline:
                if (a.output/'stop-requested.json').exists():
                    state['stop_requested_by_reader']=True;return
                if not state['identity_verified']:
                    if time.monotonic()>identity_deadline:raise TimeoutError('ESP identity not received')
                    if time.monotonic()>=next_info:pc.write(b'INFO\n');next_info=time.monotonic()+1
                pending.extend(pc.read(16384))
                if len(pending)>65536:raise RuntimeError('Unbounded JSON line')
                while b'\n' in pending:
                    line,_,rest=pending.partition(b'\n');pending=bytearray(rest)
                    if not line.strip():continue
                    try:e=json.loads(line)
                    except ValueError:e={'event':'non_json','hex':bytes(line).hex()}
                    log.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'event':e})+'\n');log.flush()
                    kind=e.get('event')
                    if kind=='info' and not state['identity_verified']:
                        if not(e.get('firmware') in ('sftool-bridge-v1','sftool-bridge-v2','sftool-bridge-v3','sftool-bridge-v4','sftool-bridge-v5') and e.get('rx_gpio')==4 and e.get('tx_gpio')==5 and e.get('baud')==1000000):
                            raise RuntimeError('Unexpected transport identity')
                        state['identity_verified']=True;state['initial_info']=e
                        pc.write(b'DISARM\n')
                        if a.probe:pc.write(b'ARM PROBE\n')
                        elif a.arm_bridge:
                            if e.get('known52') is not True and e.get('firmware') not in ('sftool-bridge-v2','sftool-bridge-v3','sftool-bridge-v4','sftool-bridge-v5'):
                                raise RuntimeError('Chip-ID verification not satisfied')
                            pc.write(b'ARM SFT52\n')
                        print(json.dumps(e),flush=True)
                    elif kind=='armed':
                        expected=1 if a.probe else 2 if a.arm_bridge else 0
                        if e.get('mode')!=expected:raise RuntimeError('Unexpected armed mode')
                        if a.arm_bridge and state['initial_info'].get('firmware') in ('sftool-bridge-v2','sftool-bridge-v3','sftool-bridge-v4','sftool-bridge-v5') and e.get('verify_before_forward') is not True:
                            raise RuntimeError('Missing per-boot ID guard')
                        state['armed']=True;last_arm_time=time.monotonic()
                        print('READY: '+state['action']+' armed, waiting for printer Power.',flush=True)
                    elif kind=='rom_word':
                        state.setdefault('words',[]).append(e);print(json.dumps(e),flush=True)
                    elif kind=='flash_head':
                        raw=bytes.fromhex(e['hex'])
                        if len(raw)!=32 or e.get('address')!=0x10000000:raise RuntimeError('Unexpected flash head')
                        (a.output/'flash-head-10000000.bin').write_bytes(raw)
                        state['flash_head']=e;print(json.dumps(e),flush=True)
                    elif kind=='probe_done':state['probe_done']=True;return
                    elif kind=='heartbeat':
                        state['last_heartbeat']=e
                        counters=(e.get('boots'),e.get('native_tx'),e.get('native_rx'))
                        if wait_baseline is None:wait_baseline=counters
                        # Extend waiting only while no target boot/data has occurred.
                        # Never rearm over an active transfer or a detected boot.
                        if (a.arm_bridge and a.seconds>120 and last_arm_time is not None
                                and time.monotonic()-last_arm_time>80 and counters==wait_baseline
                                and e.get('mode')==2 and e.get('stage')==0 and not e.get('active')):
                            pc.write(b'ARM SFT52\n');last_arm_time=time.monotonic()
                            state['waiting_renewals']=state.get('waiting_renewals',0)+1
                    elif kind in ('fatal','rejected','probe_timeout','native_rx_short_write'):
                        raise RuntimeError(f'Transport failure: {e}')
                    elif kind in ('enter_ack','boot_enter_tx','raw_bridge_active','expired'):
                        print(json.dumps(e),flush=True)
    except Exception as exc:state['error']=str(exc);raise
    finally:
        if pc.is_open and state['identity_verified']:
            try:pc.write(b'DISARM\n');pc.flush();state['disarm_requested']=True
            except (serial.SerialException,OSError):state['disarm_requested']=False
        pc.close();state['finished_utc']=datetime.now(timezone.utc).isoformat()
        (a.output/'summary.json').write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(state),flush=True)


if __name__=='__main__':main()
