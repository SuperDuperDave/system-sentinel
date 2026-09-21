# Reading latency

What each reading costs on the machine it was taken on, from `system-sentinel bench`. Every number
is the envelope's own `took_ms` — what the caller waited for — over the runs the section names. A
reading that was not observed carries its outcome instead of a time, because a failure is not a
measurement. Min, median and p95 are nearest-rank over the observed runs: nothing is interpolated,
so a number never implies a sample that was not taken.

Regenerate it on the machine, one run per transport:

```
system-sentinel bench --transport session --out docs/measurements/latency.md
system-sentinel bench --transport one-shot --out docs/measurements/latency.md
```

The second run does not erase the first: a run rewrites its own transport's section and carries the
other one through. The transport a section names is the one that carried its questions: `bench`
sets the pool's size before the first question and reads back what the bridge did with it, so a
question that fell back to a launch is counted on the line rather than hidden in the numbers.

A run takes the whole catalog, the heavy readings included, so it is as slow as the slowest query
on the machine: `--readings a,b` narrows it when only some are in question, and `--runs N` buys a
tail worth reading, since a p95 over three runs is only the slowest of the three.

Nothing here names a machine, a person or a path, and `bench` refuses to write a document that
does.

## Session transport

- Taken **2026-09-21**, 5 run(s) per reading, system-sentinel 1.1.0.
- Machine: Microsoft Windows 11 Pro, build 26200, 64-bit.
- Transport: **session**, a pool of 4; 146 question(s) answered through sessions, 1 fell back to a launch.
- Bridge floor (a script that reads nothing): min 8 ms, median 8 ms, p95 11 ms over 5 run(s).

| Reading | Runs | Min (ms) | Median (ms) | p95 (ms) | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| events | 5 | 78 | 80 | 177 | ok |
| record | 5 | 83 | 86 | 88 | ok |
| system | 5 | 1178 | 1187 | 1261 | ok |
| hardware | 5 | 1324 | 1354 | 1420 | ok |
| hardware.cpu | 5 | 1078 | 1093 | 1137 | ok |
| hardware.gpu | 5 | 40 | 44 | 53 | ok |
| hardware.board | 5 | 4943 | 4951 | 4997 | ok |
| hardware.storage | 5 | 101 | 103 | 1144 | ok |
| hardware.network | 5 | 836 | 874 | 4023 | ok |
| drivers | 5 | 1565 | 1602 | 1896 | ok |
| dumps | 5 | 25 | 26 | 40 | ok |
| crash | 5 | 405 | 409 | 448 | ok |
| faults | 5 | 55 | 56 | 65 | ok |
| pcie | 5 | 803 | 830 | 940 | ok |
| power | 5 | 602 | 627 | 668 | ok |
| memory | 5 | 174 | 185 | 195 | ok |
| constraints | 5 | 449 | 451 | 469 | ok |
| signals | 5 | 1302 | 1358 | 1552 | ok |
| health | 5 | 16 | 18 | 29 | ok |
| reliability | 5 | 885 | 916 | 928 | ok |
| whea | 5 | 69 | 72 | 77 | empty |
| storms | 5 | 73 | 74 | 80 | empty |

## One-shot transport

- Taken **2026-09-21**, 5 run(s) per reading, system-sentinel 1.1.0.
- Machine: Microsoft Windows 11 Pro, build 26200, 64-bit.
- Transport: **one-shot**, a process per question.
- Bridge floor (a script that reads nothing): min 232 ms, median 234 ms, p95 240 ms over 5 run(s).

| Reading | Runs | Min (ms) | Median (ms) | p95 (ms) | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| events | 5 | 380 | 400 | 424 | ok |
| record | 5 | 400 | 404 | 411 | ok |
| system | 5 | 1494 | 1521 | 1559 | ok |
| hardware | 5 | 1680 | 1692 | 1742 | ok |
| hardware.cpu | 5 | 1415 | 1442 | 1467 | ok |
| hardware.gpu | 5 | 380 | 393 | 415 | ok |
| hardware.board | 5 | 5325 | 5338 | 5397 | ok |
| hardware.storage | 5 | 1309 | 1324 | 1361 | ok |
| hardware.network | 5 | 3806 | 3862 | 4027 | ok |
| drivers | 5 | 1872 | 1902 | 2023 | ok |
| dumps | 5 | 302 | 316 | 324 | ok |
| crash | 5 | 772 | 797 | 834 | ok |
| faults | 5 | 353 | 358 | 372 | ok |
| pcie | 5 | 1306 | 1347 | 1460 | ok |
| power | 5 | 988 | 1015 | 1089 | ok |
| memory | 5 | 548 | 616 | 819 | ok |
| constraints | 5 | 1003 | 1059 | 1118 | ok |
| signals | 5 | 1849 | 1972 | 1987 | ok |
| health | 5 | 293 | 297 | 321 | ok |
| reliability | 5 | 1337 | 1410 | 1719 | ok |
| whea | 5 | 281 | 292 | 340 | empty |
| storms | 5 | 328 | 333 | 393 | empty |
