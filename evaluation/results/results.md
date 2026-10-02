# Detection results (test set)

Test set: 12 days x 6 AHUs, 50 injected faults (10 of each type), generated with its own random seed. All thresholds and design choices were made on a separate 5-day validation set; the test set was evaluated once.

| Configuration | Faults detected | Median time to detect | False alarms | False alarms per AHU-day | Event precision | Reading-level F1 |
| --- | --- | --- | --- | --- | --- | --- |
| Rules only | 46/50 | 14 min | 1 | 0.014 | 98% | 0.72 |
| z-score only | 45/50 | 9 min | 7 | 0.097 | 88% | 0.77 |
| Isolation Forest only | 33/50 | 50 min | 0 | 0.000 | 100% | 0.50 |
| Full system | 50/50 | 8 min | 6 | 0.083 | 89% | 0.80 |
| Full, no weather check | 50/50 | 8 min | 7 | 0.097 | 88% | 0.80 |
| Full, no LOW hold | 50/50 | 8 min | 12 | 0.167 | 81% | 0.80 |

## Full system, per fault type

| Fault | Detected | Median time to detect | Slowest |
| --- | --- | --- | --- |
| Compressor failure | 10/10 | 3 min | 26 min |
| After-hours waste | 10/10 | 3 min | 3 min |
| Sensor stuck | 10/10 | 8 min | 8 min |
| Filter blockage | 10/10 | 9 min | 12 min |
| Refrigerant leak | 10/10 | 106 min | 282 min |
