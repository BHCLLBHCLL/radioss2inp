# radioss2inp / radioss2vtk

OpenRadioss Starter 输入文件（`.rad`）转换工具集：

| 脚本 | 输出 | 用途 |
| ---- | ---- | ---- |
| `radioss2inp.py` | Abaqus 6.14 `.inp` | 显式动力学求解 |
| `radioss2vtk.py` | VTK `.vtk` / `.vtu` | 网格可视化（ParaView 等） |

## 功能特性

### radioss2inp（.rad → .inp）

- 解析 OpenRadioss 的关键字格式（`/KEY`、`/KEY/SUBKEY/ID`）
- 自动映射 17 类 Radioss 关键字到等价的 Abaqus 关键字
- 内置 13 项 Abaqus 输入规范验证（节点/单元唯一性、连通性长度、材料引用、塑性表合法性等）
- 验证报告以注释形式附加到 `.inp` 文件末尾，并同时在控制台输出
- 转换大数据量模型（已验证：257,099 节点 / 130,218 单元 / 39 MB 输出）

### radioss2vtk（.rad → .vtk）

- 与 `radioss2inp.py` 相同的流式解析策略，只提取几何网格
- 支持 `/BRICK`（HEX8）、`/TETRA10`（二次四面体）、`/RWALL/PLANE`（合成地板四边形）
- 输出 Legacy ASCII `.vtk` 或二进制 `.vtu`；附带 `PartId` / `MaterialId` / `ElementId` 场数据
- 已验证：257,103 节点 / 130,219 单元（含 1 个刚性墙 QUAD）

## 环境要求

- Python 3.8+（仅使用标准库，无需安装第三方依赖）
- 操作系统：Windows / Linux / macOS 均可

## 快速开始

### 1. 转换 .rad → .inp（Abaqus）

```bash
# 默认输入输出
python radioss2inp.py

# 指定输入输出
python radioss2inp.py input.rad output.inp
```

默认行为：

| 参数     | 默认值                       |
| -------- | ---------------------------- |
| 输入文件 | `Cell_Phone_Drop_0000.rad`   |
| 输出文件 | `Cell_Phone_Drop.inp`        |

### 1b. 转换 .rad → .vtk（可视化）

```bash
# 默认：Cell_Phone_Drop_0000.rad → Cell_Phone_Drop.vtk
python radioss2vtk.py

# 指定输入输出
python radioss2vtk.py input.rad output.vtk

# 二进制 VTU（体积更小，ParaView 加载更快）
python radioss2vtk.py input.rad output.vtu --vtu

# TETRA10 降为 4 节点线性四面体
python radioss2vtk.py input.rad output.vtk --linear-tet
```

| 参数 | 默认值 |
| ---- | ------ |
| 输入文件 | `Cell_Phone_Drop_0000.rad` |
| 输出文件 | `Cell_Phone_Drop.vtk` |

**ParaView 提示**：用 `PartId` 着色区分部件；刚性墙（地板）的 `PartId = 90001`。

### 2. 运行 Abaqus 计算

**重要**：在 PowerShell 管道/重定向环境下直接调用 `abq6142.exe` 会挂起（CPU=0，等待交互式控制台）。推荐用以下两种方式之一运行。

#### 方式 A：在 CMD 终端中直接运行（推荐）

打开独立的 CMD 窗口（不是 PowerShell 管道），执行：

```bat
cd /d d:\training\caedecoder\radioss2inp
abaqus job=Cell_Phone_Drop interactive ask_delete=off mp_mode=threads cpus=4 double=both
```

#### 方式 B：通过 Windows 计划任务运行（自动化场景）

使用仓库提供的 `run_abaqus.bat`：

```powershell
$action = New-ScheduledTaskAction -Execute "d:\training\caedecoder\radioss2inp\run_abaqus.bat" `
    -WorkingDirectory "d:\training\caedecoder\radioss2inp"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName "AbaqusCellPhone2" -Action $action `
    -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "AbaqusCellPhone2"
```

#### 关键运行参数

| 参数 | 取值 | 说明 |
| ---- | ---- | ---- |
| `job` | `Cell_Phone_Drop` | 任务名（自动使用同名 `.inp` 文件） |
| `interactive` | （无值） | 前台交互模式，触发求解器运行 |
| `ask_delete=off` | —— | 覆盖旧输出文件不询问 |
| `mp_mode=threads` | —— | **避免 MPI 挂起**（默认 MPI 模式在某些环境下会卡住） |
| `cpus=4` | —— | 4 线程并行 |
| `double=both` | —— | 双精度输出 |

**避坑指南**：

- ❌ 不要使用 `input=Cell_Phone_Drop.inp` 显式参数（会让 Abaqus 误解析为 datacheck 模式，导致 `.com` 中 `runCalculator:OFF`，求解器不运行）
- ❌ 不要在 PowerShell 中用 `& abaqus ...` 或 `Start-Process -RedirectStandardOutput` 启动（`abq6142.exe` 会挂起 CPU=0）
- ❌ 不要在 PowerShell 中用 `abaqus ... | Out-File` 管道方式（同上）
- ✅ 正确做法：CMD 终端直接运行 / 计划任务 / Abaqus CAE GUI

### 3. 检查 ODB 结果

使用 `check_odb.py` 验证 ODB 是否包含应力/应变/位移等结果数据（需用 `abaqus python` 运行以获得 `odbAccess` 模块）：

```powershell
# 通过计划任务运行（避免 PowerShell 挂起）
$action = New-ScheduledTaskAction -Execute "d:\training\caedecoder\radioss2inp\check_odb.bat" `
    -WorkingDirectory "d:\training\caedecoder\radioss2inp"
Register-ScheduledTask -TaskName "OdbCheck" -Action $action -Force | Out-Null
Start-ScheduledTask -TaskName "OdbCheck"
# 结果输出到 odb_check_result.txt
```

或在 CMD 终端中直接运行：

```bat
abaqus python check_odb.py Cell_Phone_Drop.odb
```

检查脚本会验证 6 类必须结果：`S`（应力）、`LE`（应变）、`PEEQ`（等效塑性应变）、`U`（位移）、`V`（速度）、`A`（加速度）。

### 4. 诊断 ODB 无结果数据问题

如果 ODB 导入 Abaqus CAE 时报 `There is no valid step data available on the database`，按以下顺序排查：

1. **检查 `.com` 文件**：搜索 `runCalculator`
   - `OFF` → 求解器未运行（检查是否误用了 `input=` 参数）
   - `ON` → 求解器已触发，继续下一步
2. **检查 `.sta` 文件**：是否出现 `SOLUTION PROGRESS` 和 `STEP 1 ORIGIN`
   - 没有 → 求解器卡在域分解阶段，尝试 `mp_mode=threads` 或 `cpus=1`
3. **检查 `.dat` 文件**：结尾是否有 `ANALYSIS PHASE`
   - 只有 `END OF USER INPUT PROCESSING` → 输入处理完成但求解器未启动
4. **检查进程**：`explicit_dp.exe` 是否在运行
   - 不在 → 说明求解器从未启动（通常是 `abq6142.exe` 挂起）
   - 在但 CPU=0 → 求解器卡住，重启并改用计划任务方式

## 关键字映射表

| Radioss 关键字                | Abaqus 关键字                                                | 说明                                  |
| ----------------------------- | ------------------------------------------------------------ | ------------------------------------- |
| `/BEGIN` + `/TITLE`           | `*HEADING`                                                   | 标题与单位注释                        |
| `/NODE`                       | `*NODE`                                                      | 节点坐标                              |
| `/BRICK`                      | `*ELEMENT, TYPE=C3D8`                                        | 8 节点六面体                          |
| `/TETRA10`                    | `*ELEMENT, TYPE=C3D10M`                                      | 10 节点四面体（节点顺序重排）         |
| `/PART` + `/PROP/SOLID`       | `*SOLID SECTION`                                              | 每个部件生成一个 section              |
| `/MAT/ELAST`                  | `*MATERIAL` + `*ELASTIC` + `*DENSITY`                         | 线弹性材料                            |
| `/MAT/PLAS_TAB`               | `*MATERIAL` + `*ELASTIC` + `*PLASTIC` + `*DENSITY`           | 表格式塑性（引用 `/FUNCT`）           |
| `/FUNCT`                      | `*AMPLITUDE`                                                 | 函数表                                |
| `/GRNOD/NODE`                 | `*NSET`                                                      | 节点集                                |
| `/INIVEL/TRA`                 | `*INITIAL CONDITIONS, TYPE=VELOCITY`                          | 初始平动速度                          |
| `/GRAV`                       | `*DLOAD, AMPLITUDE=...`（GRAV 载荷，step level，作用于 `ALL_ELEMS`） | 重力 / 体加速度载荷（引用 `/FUNCT` 幅值表） |
| `/INTER/TYPE2`                | `*TIE, ADJUST=NO` + `*SURFACE, TYPE=NODE`                    | 绑定接触（禁止自动调整 slave 节点）   |
| `/RWALL/PLANE`                | `R3D4` 离散刚性单元 + `*RIGID BODY` + `*CONTACT PAIR, MECHANICAL CONSTRAINT=PENALTY` | 平面刚性墙 + Penalty 接触对（与 `*TIE` 及多域并行兼容） |
| （自动生成）                  | `*STEP` + `*DYNAMIC, EXPLICIT`（4 字段数据行）               | 显式动力学分析步                      |

### radioss2vtk 关键字映射

| Radioss 关键字 | VTK 输出 | 说明 |
| ------------ | -------- | ---- |
| `/NODE` | `POINTS` | 全部节点坐标 |
| `/BRICK` | `VTK_HEXAHEDRON` (12) | 8 节点六面体 |
| `/TETRA10` | `VTK_QUADRATIC_TETRA` (24) | 10 节点四面体（Radioss 原生顺序） |
| `/PART` | `CellData: PartId`, `MaterialId` | 部件 / 材料 ID |
| `/RWALL/PLANE` | `VTK_QUAD` (9) + 4 合成角点 | 400×400 mm 地板（`PartId = 90001`） |

## radioss2inp 验证项

转换结束后，脚本会自动执行下列 13 项检查，并将结果写入 `.inp` 末尾的注释段：

1. 节点 ID 唯一性
2. 节点 ID ≥ 1
3. 单元 ID 唯一性
4. 单元 ID ≥ 1
5. 单元连通性长度匹配（C3D8=8、C3D10M=10）
6. `*SOLID SECTION` 引用的材料已定义
7. `*SOLID SECTION` 引用的属性已定义（警告级）
8. `*PLASTIC` 表第一行塑性应变为 0（警告级）
9. `*PLASTIC` 表塑性应变单调递增（警告级）
10. 材料密度 > 0
11. 单元引用的所有节点 ID 在 `*NODE` 中存在
12. `*TIE` 引用的 slave `GRNOD` 与 master `SURF` 均已定义
13. 材料命名符合 Abaqus 规范（字母开头、字母/数字/下划线）

## radioss2inp 输出文件结构

转换后的 `.inp` 文件按以下顺序组织：

```
*HEADING                                    标题与单位
*PREPRINT, ECHO=NO, ...                     抑制 feinput echo（MODEL/HISTORY/CONTACT=NO）
*NODE                                       节点坐标
*ELEMENT, TYPE=C3D8/C3D10M                   单元（每个 PART 一段）
*SOLID SECTION                              截面属性（每个 PART 一段）
*ELSET, ELSET=ALL_ELEMS, GENERATE            全局可变形单元集（紧接单元段之后）
*MATERIAL / *ELASTIC / *PLASTIC / *DENSITY  材料定义
*NSET                                       节点集（来自 /GRNOD/NODE）
*AMPLITUDE                                  幅值表（来自 /FUNCT，供 *DLOAD 引用）
*INITIAL CONDITIONS, TYPE=VELOCITY           初始速度（每 DOF 一行：node, dof, value）
*TIE, ADJUST=NO                             绑定约束（来自 /INTER/TYPE2）
*NSET (ALL_NODES)                           全模型节点集（接触 slave 面）
*SURFACE / *RIGID BODY / *BOUNDARY          刚性墙（R3D4 离散刚性面，model level）
*SURFACE INTERACTION                        接触属性（model level）
*STEP, NLGEOM=YES                          分析步
  *DYNAMIC, EXPLICIT                       动力学参数（4 字段：, T, , Δtmax）
  *BULK VISCOSITY                          体积粘性
  *CONTACT PAIR, MECHANICAL CONSTRAINT=PENALTY  接触对（step level，Penalty 算法）
  *DLOAD, AMPLITUDE=... (GRAV)             重力载荷（step level，引用 /FUNCT 幅值）
  *OUTPUT, FIELD, NUMBER INTERVAL=20
  *ELEMENT OUTPUT, ELSET=ALL_ELEMS         单元输出（S, LE, PEEQ；排除 R3D4）
  *NODE OUTPUT                             节点输出（U, V, A）
*END STEP
** VERIFICATION REPORT                       验证报告（注释）
```

## radioss2vtk 输出结构

```
POINTS n double                             节点坐标
CELLS / CELL_TYPES                          单元连通性与 VTK 类型
POINT_DATA
  NodeId                                    原始 Radioss 节点 ID
CELL_DATA
  PartId                                    部件 ID（刚性墙 = 90000 + rw_id）
  MaterialId                                材料 ID
  ElementId                                 单元 ID
```

## 已知简化

### radioss2inp

- `*TIE` 的 master 面采用基于节点的 `*SURFACE, TYPE=NODE`，而非基于单元面的 surface；使用 `ADJUST=NO` 禁止 TIE 自动调整 slave 节点。若出现穿透或刚度过大问题，可改写为基于 SNEG/SPOS 单元面的 surface。
- `ALL_ELEMS` 在单元段之后、材料段之前生成（`*ELSET, GENERATE`），仅含 C3D8/C3D10M 可变形单元。
- `/GRAV` 转换的 `*DLOAD` 位于 `*STEP` 内，并通过 `AMPLITUDE=` 引用 `/FUNCT` 生成的幅值表。
- `*PREPRINT, ECHO=NO, MODEL=NO, HISTORY=NO, CONTACT=NO` 用于抑制 feinput 大量 echo，便于阅读 `.dat`。
- `*ELEMENT OUTPUT` 限定为 `ELSET=ALL_ELEMS`，避免对 R3D4 刚性单元请求 S/LE/PEEQ。
- 刚性墙的接触 slave 面 = `ALL_NODES`（全模型节点，显式列出所有节点 ID，因节点 ID 非连续，不能用 `GENERATE`）。覆盖范围较保守，运行时如需加速可改为各 PART 表面节点集。
- 刚性墙使用单个 R3D4 单元（4 个角节点）表示；如需更精细的几何，可改为多个 R3D4 单元组成的网格。
- 刚性墙 master 面使用 `SPOS` 标识符（R3D4 仅接受 `SPOS`/`SNEG`/`E1..E4`，不接受 `S1..S6`）。
- 未转换 `/DEFAULT/INTER/TYPE2` 与 `/DEF_SOLID` 控制卡片（Abaqus 在 `*SOLID SECTION` 与各 `*TIE` 中分别指定即可）。
- `/MAT/PLAS_TAB` 仅支持 `N_funct=1` 的单函数塑性表；多函数率相关塑性未转换。
- `*DYNAMIC, EXPLICIT` 数据行使用 Abaqus/Explicit 自动时间增量的 **4 字段**格式：`, time_period, , max_increment`（当前为 `, 0.001, , 1.0e-6`）。注意不是 2 字段或 3 字段写法。
- `*STEP` 不使用 `INC` 参数（Abaqus/Explicit 不支持，仅 Standard 支持）。
- 刚性墙 `*CONTACT PAIR` 使用 `MECHANICAL CONSTRAINT=PENALTY`（默认运动学接触与 `*TIE` 冲突，且会导致多域 MPI 并行分解失败）。
- 多核并行若仍报 `The requested number of domains cannot be created`，可改用单域运行：`abaqus job=Cell_Phone_Drop cpus=1 interactive`。

### radioss2vtk

- TETRA10 保留 Radioss 原生节点顺序（**不做** Abaqus C3D10M 重排）；ParaView 显示异常时可试 `--linear-tet`。
- `/RWALL/PLANE` 在 `.rad` 中无网格，脚本按平面方程合成 400×400 mm 四边形地板（与 `radioss2inp` R3D4 几何一致）。
- 不转换材料、载荷、接触、TIE 等非几何信息。
- Legacy `.vtk` ASCII 对大模型体积较大，建议用 `.vtu`。

## 文件说明

| 文件                          | 说明                                  |
| ----------------------------- | ------------------------------------- |
| `radioss2inp.py`              | .rad → .inp 转换脚本（含验证器）      |
| `radioss2vtk.py`              | .rad → .vtk / .vtu 网格可视化脚本     |
| `Cell_Phone_Drop_0000.rad`   | OpenRadioss 输入文件示例（42 MB）    |
| `Cell_Phone_Drop.inp`         | 转换生成的 Abaqus 输入文件（39 MB）   |
| `Cell_Phone_Drop.vtk`         | 转换生成的 VTK 网格（可视化）         |
| `run_abaqus.bat`              | Abaqus 运行批处理（计划任务调用）     |
| `check_odb.py`                | ODB 结果检查脚本（`abaqus python` 运行） |
| `check_odb.bat`               | ODB 检查批处理（计划任务调用）        |
| `DEV_SUMMARY.md`              | 开发总结文档                          |

## 使用许可

本仓库仅包含转换脚本，示例 `.rad` 文件来自 OpenRadioss 官方示例（CC BY-NC 4.0）。
