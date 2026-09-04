# NOOP Android — Room schema (extract 2026-09-04)

Source: `Gjusev/noop` fork — `android/app/src/main/java/com/noop/data/Entities.kt` + `WhoopDatabase.kt`.
Room schema **version 36**; 26 entities / 23+ tables. Columns = name/type.

**Sync-relevant discovery:** `HrSample` already carries a `synced: Int` column — the schema is
sync-aware from the start; our sync watermark can build on per-table monotonic `ts` columns plus
existing flags rather than a parallel shadow table (verify semantics of `synced` before reusing).

**Server mapping (Somatriq):** `hrSample`/`ppgHrSample`→`timeseries.heart_rate` ·
`rrInterval`→`timeseries.rr_intervals` · `skinTempSample`→`timeseries.skin_temperature` ·
`spo2Sample`→`timeseries.spo2` · `respSample`→`timeseries.respiration` · `battery`→`timeseries.battery` ·
`stepSample`→activity · `sleepStateSample`+`sleepSession`→`health.sleep_*` · `workout`→`health.workouts` ·
`dailyMetric`→vendor-score Observations · `journal`→`health.journal_entries` ·
`device`/`pairedDevice`→`identity.*` · `event`/`dayOwnership`→provenance + day attribution (ADR 0017).
`ppgWaveformSample`/`gravitySample`/`v18AuxSample` are high-frequency → raw-archive territory (§57).

## AppleDaily → `appleDaily` (10 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `day` | String |
| `steps` | Int? |
| `activeKcal` | Double? |
| `basalKcal` | Double? |
| `vo2max` | Double? |
| `avgHr` | Int? |
| `maxHr` | Int? |
| `walkingHr` | Int? |
| `weightKg` | Double? |

## AppleStepHour → `appleStepHour` (3 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `steps` | Int |

## BatterySample → `battery` (6 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `soc` | Double? |
| `mv` | Int? |
| `charging` | Boolean? |
| `synced` | Int |

## DailyMetric → `dailyMetric` (23 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `day` | String |
| `totalSleepMin` | Double? |
| `efficiency` | Double? |
| `deepMin` | Double? |
| `remMin` | Double? |
| `lightMin` | Double? |
| `disturbances` | Int? |
| `restingHr` | Int? |
| `avgHrv` | Double? |
| `recovery` | Double? |
| `strain` | Double? |
| `exerciseCount` | Int? |
| `spo2Pct` | Double? |
| `skinTempDevC` | Double? |
| `respRateBpm` | Double? |
| `steps` | Int? |
| `activeKcalEst` | Double? |
| `spo2Red` | Int? |
| `spo2Ir` | Int? |
| `avgSdnn` | Double? |
| `skinTempC` | Double? |
| `sleepHrOnly` | Boolean? |

## DeviceRow → `device` (5 cols)

| column | type |
|---|---|
| `id` | String |
| `mac` | String? |
| `name` | String? |
| `firstSeen` | Long? |
| `lastSeen` | Long? |

## DismissedSleep → `dismissedSleep` (4 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `startTs` | Long |
| `endTs` | Long |
| `managementVisible` | Boolean |

## DismissedWorkout → `dismissedWorkout` (3 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `startTs` | Long |
| `endTs` | Long |

## EventRow → `event` (5 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `kind` | String |
| `payloadJSON` | String |
| `synced` | Int |

## GravitySample → `gravitySample` (7 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `x` | Double |
| `y` | Double |
| `z` | Double |
| `synced` | Int |
| `dynAccel` | Double? |

## HrSample → `hrSample` (4 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `bpm` | Int |
| `synced` | Int |

## JournalEntry → `journal` (6 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `day` | String |
| `question` | String |
| `answeredYes` | Boolean |
| `notes` | String? |
| `numericValue` | Double? |

## LabMarkerRow → `labMarker` (12 cols)

| column | type |
|---|---|
| `id` | String |
| `deviceId` | String |
| `markerKey` | String |
| `category` | String |
| `day` | String |
| `takenAt` | Long |
| `value` | Double? |
| `valueText` | String? |
| `unit` | String |
| `source` | String |
| `note` | String? |
| `referenceText` | String? |

## LiveSessionRow → `liveSession` (12 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `startTs` | Long |
| `endTs` | Long? |
| `chargeAtStart` | Double? |
| `floorBpm` | Double |
| `ceilingBpm` | Double |
| `inBandSec` | Double |
| `belowSec` | Double |
| `aboveSec` | Double |
| `pushCount` | Int |
| `easeCount` | Int |
| `hrSource` | String |

## MetricSeriesRow → `metricSeries` (4 cols)

| column | type |
|---|---|
| `key` | String |
| `deviceId` | String |
| `day` | String |
| `value` | Double |

## PpgHrSample → `ppgHrSample` (5 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `bpm` | Int |
| `conf` | Double |
| `synced` | Int |

## PpgWaveformSampleEntity → `ppgWaveformSample` (4 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `samples` | ByteArray |
| `burstIndex` | Int? |

## RespSample → `respSample` (4 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `raw` | Int |
| `synced` | Int |

## RrInterval → `rrInterval` (8 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `rrMs` | Int |
| `seq` | Int |
| `synced` | Int |
| `ord` | Int? |
| `srcChannel` | Int? |
| `tsSuspect` | Int? |

## ScoreInputProvenanceRow → `scoreInputProvenance` (4 cols)

| column | type |
|---|---|
| `key` | String |
| `deviceId` | String |
| `day` | String |
| `sourceId` | String |

## SkinTempSample → `skinTempSample` (6 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `raw` | Int |
| `synced` | Int |
| `aux1Raw` | Int? |
| `aux2Raw` | Int? |

## SleepSession → `sleepSession` (12 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `startTs` | Long |
| `endTs` | Long |
| `efficiency` | Double? |
| `restingHr` | Int? |
| `avgHrv` | Double? |
| `stagesJSON` | String? |
| `userEdited` | Boolean |
| `startTsAdjusted` | Long? |
| `motionJSON` | String? |
| `sleepStateJSON` | String? |
| `stagingSparse` | Boolean? |

## SleepStateSampleEntity → `sleepStateSample` (4 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `state` | Int |
| `rawByte` | Int? |

## Spo2Sample → `spo2Sample` (5 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `red` | Int |
| `ir` | Int |
| `synced` | Int |

## StepSample → `stepSample` (5 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `counter` | Int |
| `activityClass` | Int? |
| `synced` | Int |

## V18AuxSampleEntity → `v18AuxSample` (3 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `ts` | Long |
| `fields` | ByteArray |

## WorkoutRow → `workout` (15 cols)

| column | type |
|---|---|
| `deviceId` | String |
| `startTs` | Long |
| `endTs` | Long |
| `sport` | String |
| `source` | String |
| `durationS` | Double? |
| `energyKcal` | Double? |
| `avgHr` | Int? |
| `maxHr` | Int? |
| `strain` | Double? |
| `distanceM` | Double? |
| `zonesJSON` | String? |
| `notes` | String? |
| `routePolyline` | String? |
| `steps` | Int? |
