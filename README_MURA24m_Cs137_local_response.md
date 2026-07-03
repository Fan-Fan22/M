# MURA24m Cs-137 局部相机响应库任务包

本包用于基于 `MURA24m.i` 探测器/编码板几何建立 Cs-137 局部相机响应库：

\[
H_{662}(r, \alpha, \beta, e, p)
\]

当前配置为单能窗：

- 源能量：0.662 MeV
- 能窗：0.50–0.80 MeV
- 探测器：19×19 = 361 像素，cell 1001–1361
- Tally：F18:P pulse-height tally
- 相机前方方向：local -z
- 输出矩阵维度：`[Nr, Nalpha, Nbeta, Nwin, Npixel]`

## 1. 本次网格设置

`configs/config_MURA24m_E662_w050_080_customR_beta75.json` 中设置为：

```text
r_grid_cm = [
  30, 40, 50, 60, 70, 80, 90, 100,
  125, 150, 175, 200,
  225, 250, 275, 300,
  350, 400, 450,
  500, 600, 700
]
alpha = -45° ~ +45°, step = 2.5°
beta  = -45° ~ +75°, step = 2.5°
```

数量：

```text
Nr = 22
Nalpha = 37
Nbeta = 49
总 MCNP 源点 = 22 × 37 × 49 = 39886
```

## 2. 源点坐标定义

沿用你的旧代码约定：相机前方是 local -z。

给定 `(r, alpha, beta)`，MCNP 点源坐标为：

```text
d = r / sqrt(1 + tan(alpha)^2 + tan(beta)^2)
x = d * tan(alpha)
y = d * tan(beta)
z = -d
```

所以源在相机前方时 `z < 0`。

## 3. 文件说明

```text
templates/MURA24m_original.i
    你上传的原始 MCNP 文件。

templates/MURA24m_f8_E662_w050_080.i
    已追加 F18/E18 的模板，用于 Cs-137 0.50–0.80 MeV 响应库。

configs/config_MURA24m_E662_w050_080_customR_beta75.json
    主配置文件。当前已设定 `mcnp_exe=D:/mcnp6/mc6mpi.exe`、`mpi_np=16`、`max_workers=8`、`run_mcnp=false`。

manifests/source_points_E662_w050_080_customR_beta75.csv
    全部 39886 个源点的 r/alpha/beta 和 MCNP POS 清单。

scripts/prepare_mura24m_f8_template.py
    将原始 MURA24m.i 补成 F18 pulse-height tally 模板。

scripts/build_response_library_f8.py
    生成 MCNP 输入、运行 MCNP、解析输出、合并成 npz 响应库。

scripts/inspect_response_library.py
    检查最终 npz 响应库的维度、总响应和相对误差。
```

## 4. 推荐运行顺序

### 第一步：确认模板

```bat
bats\00_prepare_mura24m_f8_template.bat
```

正常情况下模板已经生成好了，不必重复执行。

### 第二步：检查网格和任务数量

```bat
bats\01_dry_run_grid.bat
```

只打印配置，不生成输入文件。

### 第三步：生成源点清单

```bat
bats\02_write_manifest.bat
```

### 第四步：先生成前 100 个测试输入

```bat
bats\06_generate_first_100_test_inputs.bat
```

建议你先用前 100 个任务测试 MCNP 是否能正常跑。

### 第五步：生成全部 MCNP 输入

```bat
bats\03_generate_inputs_only.bat
```

这会生成 39886 个 MCNP 输入文件，文件数量较多。

### 第六步：运行 MCNP

当前配置中：

```json
"run_mcnp": false
```

这是为了防止误触发大批量计算。若要让脚本自动运行 MCNP，把配置改成：

```json
"run_mcnp": true
```

然后执行：

```text
python scripts\build_response_library_f8.py configs\config_MURA24m_E662_w050_080_customR_beta75.json
```

也可以只生成输入后，用你自己的调度脚本分批运行。


## 4.1 当前并行配置

配置文件中已按你的要求设为：

```json
"mcnp_exe": "D:/mcnp6/mc6mpi.exe",
"use_mpiexec": true,
"mpiexec_exe": "mpiexec",
"mpi_np_flag": "-n",
"mpi_np": 16,
"max_workers": 8,
"run_mcnp": false
```

含义：

```text
单个 MCNP 源点任务：mpiexec -n 16
同时运行源点任务数：8
理论最大并行进程数：16 × 8 = 128
```

`run_mcnp=false` 表示默认不会自动启动 MCNP 大批量计算；确认无误后再改成 `true`。

### 第七步：解析 MCNP 输出并合并响应库

当所有 `.o` 输出文件存在后，执行：

```bat
bats\04_parse_outputs_only.bat
```

生成：

```text
outputs/H_E662_w050_080_MURA24m_customR_beta75.npz
```

其中：

```text
response.shape = [22, 37, 49, 1, 361]
relerr.shape   = [22, 37, 49, 1, 361]
```

### 第八步：检查响应库

```bat
bats\05_inspect_library.bat
```

重点看：

```text
response 是否有 NaN/异常值
relerr 是否过大
中心角 alpha=0, beta=0 时，总响应是否随 r 增大总体下降
```

## 5. 注意事项

1. `MURA24m_original.i` 原本含有 F14/F16 cell tally，不是 F8 pulse-height tally。本包已在模板中追加 F18/E18。
2. MCNP 输出的 tally mean 一般已经是 per source particle 响应，不要再除以 NPS。
3. 默认 NPS 为 `1e7`。如果远距离、大角度点相对误差过大，可提高到 `1e8`。
4. 这是 Cs-137 单能窗库。后续多核素建议扩展为每个能量库都保存多个统一能窗。
5. 几何一旦修改，响应库需要重新生成。
