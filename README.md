# radioss2inp

将 **OpenRadioss** 的 Starter 输入文件（`.rad`）转换为 **Abaqus 6.14** 的输入文件（`.inp`），并在转换后自动执行符合性验证。

## 功能特性

- 解析 OpenRadioss 的关键字格式（`/KEY`、`/KEY/SUBKEY/ID`）
- 自动映射 17 类 Radioss 关键字到等价的 Abaqus 关键字
- 内置 13 项 Abaqus 输入规范验证（节点/单元唯一性、连通性长度、材料引用、塑性表合法性等）
- 验证报告以注释形式附加到 `.inp` 文件末尾，并同时在控制台输出
- 转换大数据量模型（已验证：257,099 节点 / 130,218 单元 / 39 MB 输出）

## 环境要求

- Python 3.8+（仅使用标准库，无需安装第三方依赖）
- 操作系统：Windows / Linux / macOS 均可

## 快速开始

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
| `/GRAV`                       | `*DLOAD`（GRAV 加速度载荷，作用于 `ALL_ELEMS`）              | 重力 / 体加速度载荷                   |
| `/INTER/TYPE2`                | `*TIE` + `*SURFACE, TYPE=NODE`                               | 绑定接触（slave 节点集 ↔ master 面）  |
| `/RWALL/PLANE`                | `R3D4` 离散刚性单元 + `*RIGID BODY` + `*CONTACT PAIR, MECHANICAL CONSTRAINT=PENALTY` | 平面刚性墙 + Penalty 接触对（与 `*TIE` 及多域并行兼容） |
| （自动生成）                  | `*STEP` + `*DYNAMIC, EXPLICIT`（4 字段数据行）               | 显式动力学分析步                      |

## 验证项

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

## 输出文件结构

转换后的 `.inp` 文件按以下顺序组织：

```
*HEADING                                    标题与单位
*PREPRINT                                   打印控制
*NODE                                       节点坐标
*ELEMENT, TYPE=C3D8/C3D10M                   单元（每个 PART 一段）
*SOLID SECTION                              截面属性（每个 PART 一段）
*MATERIAL / *ELASTIC / *PLASTIC / *DENSITY  材料定义
*NSET                                       节点集（来自 /GRNOD/NODE）
*ELSET (ALL_ELEMS)                          全局单元集（供 *DLOAD GRAV 引用）
*AMPLITUDE                                  幅值表（来自 /FUNCT）
*INITIAL CONDITIONS, TYPE=VELOCITY           初始速度（每 DOF 一行：node, dof, value）
*TIE                                        绑定约束（来自 /INTER/TYPE2）
*NSET (ALL_NODES)                           全模型节点集（接触 slave 面）
*SURFACE / *RIGID BODY / *BOUNDARY          刚性墙（R3D4 离散刚性面，model level）
*SURFACE INTERACTION                        接触属性（model level）
*STEP, NLGEOM=YES                          分析步
  *DYNAMIC, EXPLICIT                       动力学参数（4 字段：, T, , Δtmax）
  *BULK VISCOSITY                          体积粘性
  *CONTACT PAIR, MECHANICAL CONSTRAINT=PENALTY  接触对（step level，Penalty 算法）
  *DLOAD (GRAV)                            重力载荷（step level）
  *OUTPUT / *ELEMENT OUTPUT / *NODE OUTPUT  输出请求
*END STEP
** VERIFICATION REPORT                       验证报告（注释）
```

## 已知简化

- `*TIE` 的 master 面采用基于节点的 `*SURFACE, TYPE=NODE`，而非基于单元面的 surface。若出现穿透或刚度过大问题，可改写为基于 SNEG/SPOS 单元面的 surface。
- 刚性墙的接触 slave 面 = `ALL_NODES`（全模型节点，显式列出所有节点 ID，因节点 ID 非连续，不能用 `GENERATE`）。覆盖范围较保守，运行时如需加速可改为各 PART 表面节点集。
- 刚性墙使用单个 R3D4 单元（4 个角节点）表示；如需更精细的几何，可改为多个 R3D4 单元组成的网格。
- 刚性墙 master 面使用 `SPOS` 标识符（R3D4 仅接受 `SPOS`/`SNEG`/`E1..E4`，不接受 `S1..S6`）。
- 未转换 `/DEFAULT/INTER/TYPE2` 与 `/DEF_SOLID` 控制卡片（Abaqus 在 `*SOLID SECTION` 与各 `*TIE` 中分别指定即可）。
- `/MAT/PLAS_TAB` 仅支持 `N_funct=1` 的单函数塑性表；多函数率相关塑性未转换。
- `*DYNAMIC, EXPLICIT` 数据行使用 Abaqus/Explicit 自动时间增量的 **4 字段**格式：`, time_period, , max_increment`（当前为 `, 0.001, , 1.0e-6`）。注意不是 2 字段或 3 字段写法。
- `*STEP` 不使用 `INC` 参数（Abaqus/Explicit 不支持，仅 Standard 支持）。
- 刚性墙 `*CONTACT PAIR` 使用 `MECHANICAL CONSTRAINT=PENALTY`（默认运动学接触与 `*TIE` 冲突，且会导致多域 MPI 并行分解失败）。
- 多核并行若仍报 `The requested number of domains cannot be created`，可改用单域运行：`abaqus job=Cell_Phone_Drop cpus=1 interactive`。

## 文件说明

| 文件                          | 说明                                  |
| ----------------------------- | ------------------------------------- |
| `radioss2inp.py`              | 转换脚本（含验证器）                  |
| `Cell_Phone_Drop_0000.rad`   | OpenRadioss 输入文件示例（42 MB）    |
| `Cell_Phone_Drop.inp`         | 转换生成的 Abaqus 输入文件（39 MB）   |
| `DEV_SUMMARY.md`              | 开发总结文档                          |

## 使用许可

本仓库仅包含转换脚本，示例 `.rad` 文件来自 OpenRadioss 官方示例（CC BY-NC 4.0）。
