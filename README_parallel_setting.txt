已按要求更新并行配置：

"mcnp_exe": "D:/mcnp6/mc6mpi.exe"
"use_mpiexec": true
"mpiexec_exe": "mpiexec"
"mpi_np_flag": "-n"
"mpi_np": 16
"max_workers": 8
"run_mcnp": false

说明：单个 MCNP 任务使用 16 个 MPI 进程，同时最多运行 8 个 MCNP 源点任务，理论最大并行进程数为 128。run_mcnp=false 表示默认只生成/解析，不自动启动批量 MCNP。
