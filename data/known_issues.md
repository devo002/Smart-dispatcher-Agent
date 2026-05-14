# Empire Field Service — Known Issues Reference

This document is part of the RAG corpus the Smart-Dispatcher agent searches when triaging
tickets. It complements the manufacturer PDFs in `data/manuals/`. Each entry is
self-contained so it can be chunked and embedded cleanly.

> NOTE FOR REVIEWERS: Entry **HUA-602** below is intentionally **stale** — the listed
> replacement part is discontinued in `inventory.csv`. The agent must detect the
> inventory miss and fall back to the firmware-update workaround. This drives the
> self-correction demo trace.

---

## HUA-001 — Huawei SUN2000 — Error 0x0001 (Grid Loss)

**Symptom.** Inverter shows red status LED, app reports "Grid Lost," PV production stops.
**Root cause.** Grid voltage outside acceptable window (most often a utility-side outage
or a tripped main RCD at the customer property).
**Field fix.** No part swap needed. Verify the household main breaker, then wait 5
minutes for the inverter to auto-restart. If the issue persists more than 30 minutes,
escalate to grid operator.
**Required part.** None.
**Severity.** Low.

## HUA-501 — Huawei SUN2000 — Error 501 (DC Insulation Fault)

**Symptom.** Yellow status LED, app shows "DC Insulation Resistance Low."
**Root cause.** Moisture ingress at MC4 connector or damaged PV string cable.
**Field fix.** Inspect string-side MC4 connectors for water; reseat with new gaskets.
Measure insulation resistance with megger; replace damaged cable section if reading
below 1 MOhm.
**Required part.** `MC4-GASKET-KIT` (in stock) or `PV-CABLE-6MM-50M` (in stock).
**Severity.** Medium. Schedule within 48h.

## HUA-602 — Huawei SUN2000 — Error 602 (Grid Overvoltage, Sustained)

**Symptom.** Repeated red-LED faults during midday peak, app shows "Grid Voltage High."
**Root cause.** AC-side voltage rising above 253V due to undersized AC cable on long
runs, common on installations >25m from the meter.
**Field fix (PRIMARY).** Replace the AC isolator with the upgraded `HUA-AC-ISO-V2`
unit which includes built-in voltage compensation.
**Required part.** `HUA-AC-ISO-V2`.
**Severity.** Medium.

## SMA-301 — SMA Sunny Boy — Error 301 (Self-test Fault)

**Symptom.** Inverter cycles through self-test repeatedly, no production.
**Root cause.** Failed AFCI module, typically after a lightning event in the region.
**Field fix.** Replace the AFCI module on the AC board.
**Required part.** `SMA-AFCI-MOD`.
**Severity.** High. Schedule within 24h — system is offline.

## SMA-410 — SMA Sunny Boy — Warning 410 (Fan Speed Anomaly)

**Symptom.** Audible fan grinding, occasional thermal derating in summer.
**Root cause.** Bearing wear on the internal cooling fan.
**Field fix.** Replace cooling fan assembly.
**Required part.** `SMA-FAN-ASSY`.
**Severity.** Low. Schedule at next available slot, system still operating.

## FRO-1010 — Fronius Symo — State 1010 (DC Component Issue)

**Symptom.** Inverter restarts every few hours, customer reports flickering output.
**Root cause.** Failing DC capacitor bank, common on units >5 years old.
**Field fix.** Capacitor replacement requires factory-trained technician — escalate
to certified Fronius partner. Do not attempt field repair.
**Required part.** `FRO-CAP-BANK` (workshop only, not field-replaceable).
**Severity.** High. System should be powered down until repair.

## GOO-115 — Goodwe GW-ET — Error 115 (Battery Communication Lost)

**Symptom.** Hybrid system shows battery offline; PV still feeds grid normally.
**Root cause.** CAN-bus cable between inverter and battery degraded or unseated.
**Field fix.** Replace CAN cable; verify termination resistor on battery end.
**Required part.** `GOO-CAN-CABLE-2M`.
**Severity.** Medium.

## HP-VAILLANT-F22 — Vaillant aroTHERM — F.22 (Low Pressure)

**Symptom.** Heat pump shows F.22, no heating output, customer reports cold radiators.
**Root cause.** Refrigerant circuit pressure low, often due to slow leak at brazed
joint.
**Field fix.** Pressure-test the refrigerant circuit, locate leak with electronic
detector, re-braze joint and recharge with R290.
**Required part.** `R290-REFRIG-1KG` and `BRAZE-KIT-HP`.
**Severity.** High. Customer is without heat. Same-day visit required in winter months.

## HP-VIESSMANN-A9 — Viessmann Vitocal — A9 (Compressor Overload)

**Symptom.** Heat pump cycles into fault state on cold mornings, recovers mid-day.
**Root cause.** Compressor start capacitor weakening; struggles under cold-start load.
**Field fix.** Replace start capacitor on compressor board.
**Required part.** `VIE-COMP-CAP-45UF`.
**Severity.** Medium. Schedule within 5 days.

## BAT-LFP-OV — Generic LFP Battery — Overvoltage Protection Trip

**Symptom.** Battery shows fault, refuses to charge above 90% SoC.
**Root cause.** Cell imbalance after long period without full charge cycle.
**Field fix.** Initiate manual balancing cycle via installer app; allow 12h. If
imbalance persists, replace BMS.
**Required part.** None initially; `BAT-BMS-LFP` if balancing fails.
**Severity.** Low.

---

## Workarounds when primary parts are unavailable

### HUA-602 fallback (firmware route)

If `HUA-AC-ISO-V2` is out of stock, the Huawei SUN2000 firmware version **V300R001C00SPC135**
or later includes a configurable grid-voltage tolerance band that can be widened from
the default ±10% to ±13%, eliminating the overvoltage trip on long AC runs without a
hardware swap. This is an Empire-approved temporary workaround pending part
availability. The firmware update can be pushed remotely from the FusionSolar portal.

**Procedure.**
1. Confirm inverter serial supports SPC135 or later (manufactured 2022 or newer).
2. Push firmware via FusionSolar; takes ~20 minutes including reboot.
3. Set "Grid voltage upper limit" to 257V in installer settings.
4. Schedule a follow-up site visit within 60 days to install `HUA-AC-ISO-V2` once
   restocked.

### SMA-410 fallback

If `SMA-FAN-ASSY` is unavailable and ambient is mild, derating mode keeps the unit
operating at reduced output. Customer can be informed; visit can be deferred up to
14 days without performance penalty.
