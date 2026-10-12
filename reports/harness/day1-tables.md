
### Speed and memory (final_idle)

Host: {'cpu': 'Apple M4', 'mem_bytes': 17179869184, 'macos': '26.6.2'}; warm runs: 5

| Runtime | Window/overlap | Option | Load s | Cold s (note EN / eval mixed / eval ES) | Load peak GiB | Peak GiB | Note EN warm s | Note ES warm s | Eval mixed warm s | Eval ES warm s |
|---|---|---|---|---|---|---|---|---|---|---|
| onnx_q4 | 2048/512 | threads=4 | 1.01 | 1.71 / - / - | 0.70 | 2.17 | 0.35 | 0.34 | 7.84 | 7.83 |
| onnx_q4 | 2048/512 | threads=2 | 0.33 | 0.97 / - / - | 0.70 | 2.01 | 0.57 | 0.56 | 11.69 | 11.70 |
| onnx_q4 | 2048/512 | threads=4, no arena | 0.32 | 0.74 / - / - | 0.70 | 2.82 | 0.36 | 0.35 | 7.88 | 7.84 |
| mlx_8bit | 3072/1024 | clear_cache | 0.83 | 1.30 / - / - | 1.49 | 1.77 | 0.38 | 0.48 | 9.02 | 9.07 |
| mlx_8bit | 4096/1024 | cache | 0.19 | 0.63 / - / - | 1.49 | 1.85 | 0.37 | 0.35 | 5.74 | 5.70 |
| mlx_8bit | 4096/1024 | clear_cache | 0.18 | 0.64 / - / - | 1.49 | 1.71 | 0.39 | 0.38 | 6.11 | 6.06 |
| mlx_8bit | 8192/1024 | clear_cache | 0.20 | 0.68 / - / - | 1.49 | 1.73 | 0.38 | 0.37 | 3.80 | 3.65 |
| mlx_8bit | full | clear_cache | 0.19 | 0.64 / - / - | 1.49 | 1.73 | 0.38 | 0.37 | 3.83 | 3.62 |

### Speed and memory (final_idle_cold)

Host: {'cpu': 'Apple M4', 'mem_bytes': 17179869184, 'macos': '26.6.2'}; warm runs: 5

| Runtime | Window/overlap | Option | Load s | Cold s (note EN / eval mixed / eval ES) | Load peak GiB | Peak GiB | Note EN warm s | Note ES warm s | Eval mixed warm s | Eval ES warm s |
|---|---|---|---|---|---|---|---|---|---|---|
| onnx_q4 | 2048/512 | threads=2 | 0.63 | 1.29 / 12.50 / 12.50 | 0.71 | 2.01 | 0.57 | 0.55 | 11.67 | 11.69 |
| mlx_8bit | 8192/1024 | clear_cache | 0.72 | 1.21 / 4.62 / 4.46 | 1.50 | 1.75 | 0.38 | 0.37 | 3.79 | 3.65 |

### Speed and memory (idle)

Host: {'cpu': 'Apple M4', 'mem_bytes': 17179869184, 'macos': '26.6.2'}; warm runs: 5

| Runtime | Window/overlap | Option | Load s | Cold s (note EN / eval mixed / eval ES) | Load peak GiB | Peak GiB | Note EN warm s | Note ES warm s | Eval mixed warm s | Eval ES warm s |
|---|---|---|---|---|---|---|---|---|---|---|
| onnx_q4 | 2048/512 | threads=4 | 0.45 | 0.89 / - / - | 0.89 | 2.27 | 0.35 | 0.35 | 7.82 | 7.76 |
| onnx_q4 | 2048/512 | threads=2 | 0.46 | 1.12 / - / - | 0.89 | 2.11 | 0.58 | 0.57 | 11.69 | 11.73 |
| onnx_q4 | 3072/1024 | threads=4 | 0.46 | 0.89 / - / - | 0.89 | 3.57 | 0.36 | 0.34 | 11.68 | 11.81 |
| onnx_q4 | 3072/1024 | threads=2 | 0.47 | 1.14 / - / - | 0.89 | 3.34 | 0.57 | 0.56 | 17.20 | 16.82 |
| onnx_q4 | 4096/1024 | threads=4 | 0.46 | 0.90 / - / - | 0.89 | 5.35 | 0.35 | 0.34 | 8.51 | 8.53 |
| onnx_q4 | 4096/1024 | threads=2 | 0.46 | 1.14 / - / - | 0.89 | 5.24 | 0.58 | 0.56 | 11.97 | 12.01 |
| mlx_8bit | 4096/1024 | cache | 0.98 | 1.98 / - / - | 1.68 | 3.71 | 0.34 | 0.33 | 5.33 | 5.35 |
| mlx_8bit | 4096/1024 | clear_cache | 0.31 | 0.73 / - / - | 1.68 | 2.94 | 0.35 | 0.34 | 5.36 | 5.36 |
| mlx_8bit | 8192/1024 | cache | 0.31 | 0.71 / - / - | 1.68 | 5.05 | 0.35 | 0.34 | 3.36 | 3.22 |
| mlx_8bit | 8192/1024 | clear_cache | 0.71 | 1.13 / - / - | 1.68 | 3.02 | 0.35 | 0.34 | 3.37 | 3.21 |
| mlx_8bit | full | cache | 0.31 | 0.71 / - / - | 1.68 | 5.02 | 0.34 | 0.34 | 3.37 | 3.20 |
| mlx_8bit | full | clear_cache | 0.58 | 1.01 / - / - | 1.68 | 2.99 | 0.35 | 0.34 | 3.37 | 3.21 |

### Speed and memory (idle_mlx_chunked)

Host: {'cpu': 'Apple M4', 'mem_bytes': 17179869184, 'macos': '26.6.2'}; warm runs: 5

| Runtime | Window/overlap | Option | Load s | Cold s (note EN / eval mixed / eval ES) | Load peak GiB | Peak GiB | Note EN warm s | Note ES warm s | Eval mixed warm s | Eval ES warm s |
|---|---|---|---|---|---|---|---|---|---|---|
| mlx_8bit | 2560/1024 | clear_cache | 0.29 | 0.73 / - / - | 1.68 | 2.03 | 0.37 | 0.36 | 14.59 | 13.37 |
| mlx_8bit | 3072/1024 | clear_cache | 0.30 | 0.74 / - / - | 1.68 | 2.09 | 0.37 | 0.36 | 8.78 | 15.89 |
| mlx_8bit | 4096/1024 | cache | 0.30 | 0.72 / - / - | 1.68 | 2.48 | 0.36 | 0.35 | 5.58 | 5.56 |
| mlx_8bit | 4096/1024 | clear_cache | 0.29 | 0.73 / - / - | 1.68 | 2.20 | 0.37 | 0.36 | 5.87 | 5.85 |
| mlx_8bit | full | clear_cache | 0.30 | 0.74 / - / - | 1.68 | 2.58 | 0.37 | 0.36 | 3.70 | 3.53 |

### Speed and memory (pressure)

Host: {'cpu': 'Apple M4', 'mem_bytes': 17179869184, 'macos': '26.6.2'}; warm runs: 5; ballast 9.0 GiB, 4 CPU burners

| Runtime | Window/overlap | Option | Load s | Cold s (note EN / eval mixed / eval ES) | Load peak GiB | Peak GiB | Note EN warm s | Note ES warm s | Eval mixed warm s | Eval ES warm s |
|---|---|---|---|---|---|---|---|---|---|---|
| onnx_q4 | 2048/512 | threads=2 | 1.21 | 2.59 / - / - | 0.70 | 2.01 | 0.73 | 0.70 | 14.55 | 14.65 |
| mlx_8bit | 4096/1024 | clear_cache | 0.92 | 1.82 / - / - | 1.49 | 1.69 | 0.41 | 0.40 | 6.53 | 6.84 |
| mlx_8bit | 8192/1024 | clear_cache | 1.18 | 1.99 / - / - | 1.49 | 1.75 | 0.45 | 0.45 | 4.33 | 4.16 |

### Speed and memory (pressure_cold)

Host: {'cpu': 'Apple M4', 'mem_bytes': 17179869184, 'macos': '26.6.2'}; warm runs: 5; ballast 9.0 GiB, 4 CPU burners

| Runtime | Window/overlap | Option | Load s | Cold s (note EN / eval mixed / eval ES) | Load peak GiB | Peak GiB | Note EN warm s | Note ES warm s | Eval mixed warm s | Eval ES warm s |
|---|---|---|---|---|---|---|---|---|---|---|
| onnx_q4 | 2048/512 | threads=2 | 0.93 | 1.66 / 15.62 / 15.48 | 0.71 | 2.01 | 0.75 | 0.74 | 14.25 | 14.22 |
| mlx_8bit | 8192/1024 | clear_cache | 1.01 | 1.67 / 4.93 / 4.66 | 1.50 | 1.74 | 0.40 | 0.39 | 3.88 | 3.70 |

### Agreement with the fp32 reference (window 3072/1024, exact)

| Run | Doc | Token label agree | Span P vs ref | Span R vs ref | Max prob diff |
|---|---|---|---|---|---|
| ref_fp32_w3072 | note_en_500 | 1.0000 | 1.000 | 1.000 | 0.000 |
| ref_fp32_w3072 | note_es_500 | 1.0000 | 1.000 | 1.000 | 0.000 |
| ref_fp32_w3072 | eval_mixed_5000 | 1.0000 | 1.000 | 1.000 | 0.000 |
| ref_fp32_w3072 | eval_es_5000 | 1.0000 | 1.000 | 1.000 | 0.000 |
| onnx_q4_w4096 | note_en_500 | 0.9987 | 1.000 | 1.000 | 0.608 |
| onnx_q4_w4096 | note_es_500 | 0.9870 | 0.938 | 0.833 | 0.948 |
| onnx_q4_w4096 | eval_mixed_5000 | 0.9838 | 0.925 | 0.874 | 0.938 |
| onnx_q4_w4096 | eval_es_5000 | 0.9934 | 0.924 | 0.919 | 0.878 |
| onnx_q4_w2048 | note_en_500 | 0.9987 | 1.000 | 1.000 | 0.608 |
| onnx_q4_w2048 | note_es_500 | 0.9870 | 0.938 | 0.833 | 0.948 |
| onnx_q4_w2048 | eval_mixed_5000 | 0.9838 | 0.925 | 0.874 | 0.938 |
| onnx_q4_w2048 | eval_es_5000 | 0.9934 | 0.924 | 0.919 | 0.878 |
| mlx_8bit_w4096 | note_en_500 | 0.9987 | 1.000 | 1.000 | 0.332 |
| mlx_8bit_w4096 | note_es_500 | 0.9987 | 1.000 | 1.000 | 0.117 |
| mlx_8bit_w4096 | eval_mixed_5000 | 0.9966 | 0.963 | 0.985 | 0.485 |
| mlx_8bit_w4096 | eval_es_5000 | 0.9993 | 0.971 | 0.983 | 0.400 |
| mlx_8bit_w8192 | note_en_500 | 0.9987 | 1.000 | 1.000 | 0.332 |
| mlx_8bit_w8192 | note_es_500 | 0.9987 | 1.000 | 1.000 | 0.117 |
| mlx_8bit_w8192 | eval_mixed_5000 | 0.9966 | 0.965 | 0.985 | 0.347 |
| mlx_8bit_w8192 | eval_es_5000 | 0.9993 | 0.977 | 0.988 | 0.398 |
| mlx_8bit_full | note_en_500 | 0.9987 | 1.000 | 1.000 | 0.332 |
| mlx_8bit_full | note_es_500 | 0.9987 | 1.000 | 1.000 | 0.117 |
| mlx_8bit_full | eval_mixed_5000 | 0.9966 | 0.965 | 0.985 | 0.347 |
| mlx_8bit_full | eval_es_5000 | 0.9993 | 0.977 | 0.988 | 0.398 |

### Gold identifiers fully covered (Privacy Filter alone, type-agnostic)

| Run | DATE | ID | PERSON | PHONE | SCHOOL |
|---|---|---|---|---|---|
| ref_fp32_w3072 | 121/121 (100.0%) | 4/4 (100.0%) | 338/627 (53.9%) | 59/59 (100.0%) | 16/71 (22.5%) |
| onnx_q4_w4096 | 121/121 (100.0%) | 4/4 (100.0%) | 321/627 (51.2%) | 59/59 (100.0%) | 13/71 (18.3%) |
| onnx_q4_w2048 | 121/121 (100.0%) | 4/4 (100.0%) | 321/627 (51.2%) | 59/59 (100.0%) | 13/71 (18.3%) |
| mlx_8bit_w4096 | 121/121 (100.0%) | 4/4 (100.0%) | 343/627 (54.7%) | 59/59 (100.0%) | 16/71 (22.5%) |
| mlx_8bit_w8192 | 121/121 (100.0%) | 4/4 (100.0%) | 343/627 (54.7%) | 59/59 (100.0%) | 16/71 (22.5%) |
| mlx_8bit_full | 121/121 (100.0%) | 4/4 (100.0%) | 343/627 (54.7%) | 59/59 (100.0%) | 16/71 (22.5%) |

### Window comparisons

| Comparison | Doc | Token label agree | Max prob diff |
|---|---|---|---|
| mlx_8bit w4096 vs full | note_en_500 | 1.0000 | 0.00e+00 |
| mlx_8bit w4096 vs full | note_es_500 | 1.0000 | 0.00e+00 |
| mlx_8bit w4096 vs full | eval_mixed_5000 | 0.9991 | 2.73e-01 |
| mlx_8bit w4096 vs full | eval_es_5000 | 1.0000 | 2.27e-01 |
| mlx_8bit w8192 vs full | note_en_500 | 1.0000 | 0.00e+00 |
| mlx_8bit w8192 vs full | note_es_500 | 1.0000 | 0.00e+00 |
| mlx_8bit w8192 vs full | eval_mixed_5000 | 1.0000 | 0.00e+00 |
| mlx_8bit w8192 vs full | eval_es_5000 | 1.0000 | 0.00e+00 |
| onnx_q4 w2048 vs w4096 | note_en_500 | 1.0000 | 0.00e+00 |
| onnx_q4 w2048 vs w4096 | note_es_500 | 1.0000 | 0.00e+00 |
| onnx_q4 w2048 vs w4096 | eval_mixed_5000 | 1.0000 | 7.03e-03 |
| onnx_q4 w2048 vs w4096 | eval_es_5000 | 1.0000 | 1.79e-03 |
