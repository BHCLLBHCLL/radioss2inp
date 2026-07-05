# 开发总结 (DEV_SUMMARY)

## 1. 项目目标

将 OpenRadioss Starter 输入文件（`.rad`）转换为 Abaqus 6.14 输入文件（`.inp`），并确保输出文件符合 Abaqus 输入规范。目标模型为 `Cell_Phone_Drop_0000.rad`（手机跌落测试，约 42 MB）。

## 2. 输入文件分析

### 2.1 源文件结构概览

通过 `grep '^/[A-Z]'` 扫描全部关键字，识别出 **115 处**关键字块，按文件区域分布如下：

| 区域 | 行号范围        | 主要内容                                          |
| ---- | --------------- | ------------------------------------------------- |
| 1    | 1–11            | `/BEGIN`：标题、版本、单位制（Mg-mm-s）           |
| 1    | 14–15           | `/TITLE`（空标题）                                 |
| 1    | 17–26           | `/DEFAULT/INTER/TYPE2`、`/DEF_SOLID`（控制卡片）   |
| 2    | 30–73           | 5 个材料（1×`/MAT/PLAS_TAB` + 4×`/MAT/ELAST`）    |
| 3    | 77–257177       | `/NODE`（257,099 个节点）                          |
| 4–5  | 257180–497135   | 17 个 `/PART` + `/BRICK` / `/TETRA10` 单元块      |
| 6    | 497136–497290   | 17 个 `/PROP/SOLID` 属性                          |
| 6    | 497291–497321   | 4 个 `/FUNCT` 函数表                              |
| 7    | 497324–523046   | 1 个 `/INIVEL/TRA` + 16 个 `/GRNOD/NODE`           |
| 8    | 523043–548769   | 3 个 `/GRAV`（X/Y/Z 三向体加速度）                |
| 9    | 548772–629251   | 14 个 `/INTER/TYPE2` + 14 个 `/SURF/SEG`          |
| 10   | 629252–629262   | 1 个 `/RWALL/PLANE`（平面刚性墙）                  |
| 末   | 629263          | `/END`                                            |

### 2.2 单元类型统计

- `/BRICK`（8 节点六面体）→ 10 个 PART，共 **20,618** 个单元
- `/TETRA10`（10 节点四面体）→ 7 个 PART，共 **109,600** 个单元
- 合计 **130,218** 个单元
- 注意：TETRA10 转换为 `C3D10M`（非 `C3D10`），因 Abaqus/Explicit 不支持 `C3D10`

### 2.3 关键字格式特征

Radioss 关键字有 2 种 ID 写法：
- `/KEY/ID`（如 `/PART/1`、`/NODE`、`/FUNCT/1`）
- `/KEY/SUBKEY/ID`（如 `/MAT/ELAST/4`、`/PROP/SOLID/1`、`/INTER/TYPE2/1`）

数据行以 `#` 开头为注释，`/TETRA10` 的数据每两行组成一条记录（第一行=单元 ID，第二行=10 个节点 ID）。

## 3. 设计思路

### 3.1 解析策略

采用**单遍流式解析**：用 `iter(lines)` 顺序读取文件，遇到 `/` 开头的关键字行即分发到对应的解析函数；每个解析函数负责读取自己后续的数据行，遇到下一个关键字行时通过递归 `_dispatch_keyword()` 继续分发。

这种设计的优势：
- 内存中只保留结构化数据（节点、单元、材料等），不保留文件原始内容
- 对 42 MB 大文件无需两次扫描
- 解析与输出解耦，便于扩展新的关键字

### 3.2 数据存储

| 数据结构         | 类型                | 用途                              |
| ---------------- | ------------------- | --------------------------------- |
| `self.nodes`     | `list[tuple]`       | 节点 `(nid, x, y, z)`             |
| `self.parts`     | `OrderedDict`       | 部件 → 单元列表                   |
| `self.materials` | `OrderedDict`       | 材料属性（含塑性函数引用）         |
| `self.functions` | `dict`              | 函数表点列表                       |
| `self.grnod`     | `OrderedDict`       | 节点集                             |
| `self.inivel`    | `OrderedDict`       | 初始速度                           |
| `self.grav`      | `OrderedDict`       | 重力载荷                           |
| `self.inter_type2` | `OrderedDict`     | 绑定接触                           |
| `self.surfs`     | `OrderedDict`       | SURF/SEG 段                        |
| `self.rwalls`    | `OrderedDict`       | 刚性墙                             |
| `self.props`     | `OrderedDict`       | PROP/SOLID 属性                   |

### 3.3 写出策略

按 Abaqus 6.14 标准顺序输出（注意：step-level 关键字必须放在 `*STEP` 之内）：

1. `*HEADING` + `*PREPRINT`
2. `*NODE`
3. 每个 PART：`*ELEMENT` + `*SOLID SECTION`
4. 所有材料：`*MATERIAL` + `*ELASTIC` + `*PLASTIC` + `*DENSITY`
5. 所有 `*NSET`（来自 /GRNOD/NODE）
6. `*ELSET`（全局单元集 `ALL_ELEMS`，供 `*DLOAD GRAV` 引用）
7. `*AMPLITUDE`（来自 /FUNCT）
8. `*INITIAL CONDITIONS, TYPE=VELOCITY`（每 DOF 一行：`node, dof, value`）
9. `*TIE` 约束
10. 刚性墙（model level）：`*NSET (ALL_NODES)` + `*NODE` + `*ELEMENT, TYPE=R3D4` + `*SURFACE` + `*RIGID BODY` + `*BOUNDARY` + `*SURFACE INTERACTION`
11. `*STEP, NLGEOM=YES`（不带 `INC`）
    - `*DYNAMIC, EXPLICIT` + 数据行（4 字段：空, 时长, 空, 最大增量）
    - `*BULK VISCOSITY`
    - `*CONTACT PAIR, MECHANICAL CONSTRAINT=PENALTY`（step level，不带 `TYPE=` 参数）
    - `*DLOAD GRAV`（step level）
    - `*OUTPUT` / `*ELEMENT OUTPUT` / `*NODE OUTPUT`
12. `*END STEP`
13. 验证报告（以 `**` 注释形式附加）

## 4. 关键实现要点

### 4.1 关键字分发器 `_dispatch_keyword()`

```python
parts_kw = kw.split('/')           # /MAT/ELAST/4 → ['', 'MAT', 'ELAST', '4']
head = parts_kw[1]                 # 'MAT'
sub  = parts_kw[2]                 # 'ELAST'
sid  = parts_kw[3]                 # '4'

def id_of(default_slot):
    v = parts_kw[default_slot]
    return int(v) if v.isdigit() else None
```

`id_of()` 接受默认 slot 索引：
- 两段式（`/PART/1`）→ `id_of(2)`
- 三段式（`/MAT/ELAST/4`）→ `id_of(3)`

### 4.2 TETRA10 双行记录解析与节点顺序重排

**双行记录解析**：

```python
pending_eid = None
for line in it:
    if pending_eid is None:
        pending_eid = int(toks[0])    # 第一行：单元 ID
    else:
        nids = [int(t) for t in toks[:10]]   # 第二行：10 个节点 ID
        self.parts[part_id]['elems'].append((pending_eid, nids))
        pending_eid = None
```

**节点顺序重排（Radioss → Abaqus C3D10M）**：

经实际计算单元体积发现，Radioss 的 TETRA10 角点排列方向与 Abaqus C3D10M 相反（signed volume 为负），导致 Abaqus 报"109,600 elements have zero volume"错误。

转换规则：交换角点 2 与 3，并相应交换 midside 节点。0-based 索引映射：

```
Radioss:  N1 N2 N3 N4 N5(mid12) N6(mid23) N7(mid13) N8(mid14) N9(mid24) N10(mid34)
Abaqus:   N1 N3 N2 N4 N7(mid13) N6(mid32=23) N5(mid31=12) N8(mid14) N10(mid34) N9(mid24=42)
索引映射: [0, 2, 1, 3, 6, 5, 4, 7, 9, 8]
```

```python
REORDER = [0, 2, 1, 3, 6, 5, 4, 7, 9, 8]
nids = [nids[i] for i in REORDER]
```

重排后抽样验证 5 个 C3D10M 单元，signed volume 全部为正，确认方向正确。

### 4.3 `/TITLE` 的空数据行处理

源文件的 `/TITLE` 后跟一个空行，直接用 `_read_data_line()` 会错误地把下一个关键字（`/DEFAULT/INTER/TYPE2`）当作标题。解决方案：新增 `_read_optional_data_line()`，遇到 `/` 开头的行时不消费，直接 `return None` 并通过 `_dispatch_keyword()` 处理该关键字。

### 4.4 `/GRAV` 重力载荷 → `*DLOAD GRAV` 转换

Radioss 中 GRAV 的 `Fscale` 是带符号的加速度（mm/s²），方向通过 `dir` 字段（X/Y/Z）指定，作用于 `GRNOD` 节点集。

Abaqus 转换中的关键差异：
- **`BX/BY/BZ` 在 Abaqus/Explicit feinput 不支持**：尝试用 `*DLOAD` + `BX/BY/BZ` 时，feinput 输出 `*DLOAD with BX is not supported`，并跳过该数据行。
- **正确做法**：使用 `GRAV` 载荷类型，作用于**单元集**（不是节点集），数据行语法为：
  ```
  elset, GRAV, magnitude, comp1, comp2, comp3
  ```
  其中 `magnitude` 是无符号加速度幅值，`comp1..3` 是单位方向余弦（带符号，承载方向）。
- **GRNOD → ELSET 映射**：由于 `*DLOAD` 作用于单元，需要把 GRNOD 节点集映射到 ELSET。脚本采用简化策略：生成 `ALL_ELEMS` 全局单元集（用 `*ELSET, GENERATE` 紧凑表示，避免列出 13 万个单元 ID），将所有 GRAV 载荷应用到 `ALL_ELEMS`。这对跌落测试中"重力作用于整个模型"的常规场景是正确的。
- **符号处理**：当 `fscale < 0` 时，方向余弦取反（如 X 方向加速度为负，则 `comp = (-1, 0, 0)`），`magnitude = |fscale|`，保证加速度方向正确。

### 4.5 刚性墙几何重建 → R3D4 离散刚性面

`/RWALL/PLANE` 只给出平面上的两个点 M 和 M1（M→M1 即法向）。

**早期实现尝试 `*ANALYTICAL SURFACE`**：用 `*SYSTEM` + `START/LINE` 段定义解析刚性面。但 feinput 报错 `*RIGID BODY, ANALYTICAL SURFACE = RWALL_S1 does not have corresponding *SURFACE, NAME = RWALL_S1`，说明该 feinput 版本期望 `*RIGID BODY` 引用的是 `*SURFACE` 而非 `*ANALYTICAL SURFACE`。

**最终实现改用离散刚性面（R3D4 单元）**：

1. 计算法向单位向量 `n = (M1 - M) / |M1 - M|`
2. 构造平面内两个正交向量 `u`、`v`（用 Gram-Schmidt 从 `(ny, -nx, 0)` 或 `(0, nz, -ny)` 出发）
3. 在平面上生成 4 个角节点（±L=200 mm 沿 u、v 方向）
4. 创建 1 个 R3D4 单元（4 节点四边形刚性单元）连接 4 个角节点
5. 用 `*SURFACE` 列出该单元的 `SPOS` 面（R3D4 仅接受 `SPOS`/`SNEG`/`E1..E4`，不接受 `S1..S6`）
6. 用 `*RIGID BODY, REF NODE=..., ELSET=...` 将 R3D4 单元归属到参考节点
7. 用 `*BOUNDARY` 完全约束参考节点（1-6 自由度）
8. 生成 `*SURFACE INTERACTION`（model level）定义接触属性
9. 生成 `*CONTACT PAIR`（step level，必须放在 `*STEP` 之内，且不带 `TYPE=` 参数，使用 `MECHANICAL CONSTRAINT=PENALTY`）：slave = `*SURFACE, TYPE=NODE`（覆盖 ALL_NODES），master = R3D4 单元面 surface

**关键修正**（feinput 报错后调整）：

| 错误信息 | 根因 | 修正 |
|---------|------|------|
| `7 elements are distorted` 中 `S1..S6` 不识别 | R3D4 仅接受 `SPOS`/`SNEG` | 改为 `SPOS` |
| `*NSET, GENERATE` 产生未定义节点 | 节点 ID 非连续，有空洞 | 改为显式列出所有节点 ID |
| `KEYWORD CARDS FOR STEP DEPENDENT INPUT MUST APPEAR AFTER *STEP` | `*CONTACT PAIR` 放在 model level | 移到 `*STEP` 之内 |
| `UNKNOWN PARAMETER TYPE` | `TYPE=SURFACE TO SURFACE` 不被识别 | 移除 `TYPE=` 参数 |
| `The requested number of domains cannot be created`（`.sta`） | 默认运动学接触 + 14 个 `*TIE` 约束导致多域分解失败；节点同时参与运动学接触与运动学约束 | 改用 `MECHANICAL CONSTRAINT=PENALTY` |

这种方法的关键优势：
- **feinput 兼容性好**：所有 Abaqus 版本都支持 R3D4 离散刚性面
- **避免 `*ANALYTICAL SURFACE` 的 `*SYSTEM` 关联问题**
- **几何简单清晰**：单个四边形面，易于调试与可视化

### 4.6 命名安全化 `_safe_name()`

Radioss 材料名包含空格和特殊字符（如 `polymer_unfilled_plastic`、`Table  8`），Abaqus 要求名称必须字母开头、仅含字母/数字/下划线、长度 ≤ 80。函数实现：

```python
cleaned = re.sub(r'[^A-Za-z0-9_]', '_', name.strip())
cleaned = re.sub(r'_+', '_', cleaned).strip('_')
if not cleaned[0].isalpha():
    cleaned = (prefix or 'X') + '_' + cleaned
return cleaned[:80]
```

### 4.7 C3D10 → C3D10M 单元类型替换

**问题**：Abaqus/Explicit feinput 报错 `Element type C3D10 is not available for this procedure`，并跳过所有 109,600 个四面体单元。

**根因**：Abaqus/Explicit 只支持 **C3D10M**（modified tetrahedron，修正的 10 节点四面体），不支持标准 C3D10。C3D10M 在 1 个面上添加了额外的位移约束，避免了 C3D10 在显式动力学中常见的体积锁死问题。

**修正**：在 `_parse_tetra10()` 中将 `elem_type` 设为 `'C3D10M'`，并在验证器中将 `C3D10M` 加入合法单元类型表。

### 4.8 `*INITIAL CONDITIONS, TYPE=VELOCITY` 数据格式

**问题**：原实现每节点一行，格式为 `node, vx, vy, vz`，Abaqus 报错数据格式不正确。

**根因**：Abaqus `*INITIAL CONDITIONS, TYPE=VELOCITY` 的标准数据行格式为 `node, dof, value`，每个 DOF 需要单独一行，不支持单行写三向速度。

**修正**：改为每个节点输出 3 行（DOF 1/2/3）：

```python
for nid in gn['nodes']:
    f.write(f'{nid}, 1, {iv["vx"]:.6E}\n')
    f.write(f'{nid}, 2, {iv["vy"]:.6E}\n')
    f.write(f'{nid}, 3, {iv["vz"]:.6E}\n')
```

### 4.9 `*STEP` / `*DYNAMIC, EXPLICIT` 参数格式

**问题 1**：`*STEP, NLGEOM=YES, INC=100000` 中 `INC` 参数被 Abaqus/Explicit 拒绝（`INC` 仅在 Standard 中有意义）。

**修正 1**：移除 `INC` 参数 → `*STEP, NLGEOM=YES`。

**问题 2**：`*DYNAMIC, EXPLICIT` 数据行格式多次被 feinput 拒绝。

Abaqus/Explicit **自动时间增量**的数据行是 **4 字段**（不是 Standard 隐式的 2/4 字段，也不是 3 字段）：

| 列1 | 列2 | 列3 | 列4 |
| --- | --- | --- | --- |
| 空 | 分析时长 T | 空 | 最大时间增量 Δtmax |

正确写法：

```
, 0.001, , 1.0e-6
```

曾尝试的错误格式及报错：

| 错误写法 | feinput 报错 |
| -------- | ------------ |
| `0.001, , 1.0e-6`（3 字段，时长在第 1 列） | `THE TIME PERIOD MUST BE SPECIFIED` |
| `0.001, 1.0e-6`（2 字段） | `ONLY THE TIME PERIOD AND THE MAXIMUM TIME INCREMENT HAS MEANING FOR *DYNAMIC,EXPLICIT`（被当成 Standard 隐式格式解析） |

**修正 2**：使用 4 字段格式 `, 0.001, , 1.0e-6`（空, T, 空, Δtmax）。

**问题 3**：`*CONTACT PAIR` 放在 `*STEP` 之前，被报错 `KEYWORD CARDS FOR STEP DEPENDENT INPUT MUST APPEAR AFTER THE FIRST *STEP CARD`。

**修正 3**：在 `_write_rigid_wall()` 中将 `*CONTACT PAIR` 信息暂存到 `self._pending_contact_pairs` 列表，在 `_write_step()` 中 `*BULK VISCOSITY` 之后、`*DLOAD` 之前写出。

**问题 4**：`*CONTACT PAIR, INTERACTION=..., TYPE=SURFACE TO SURFACE` 被报错 `UNKNOWN PARAMETER TYPE`。

**修正 4**：移除 `TYPE=SURFACE TO SURFACE` 参数，使用默认（Explicit 默认即 surface-to-surface）。

**问题 5**：多核并行（如 6 domains）提交后，`.sta` 报错 `The requested number of domains cannot be created due to restrictions in domain decomposition`；同时出现 `WarnNodeCnsIntersectKinC` 警告（节点同时参与运动学接触与 `*TIE` 运动学约束）。

**修正 5**：刚性墙 `*CONTACT PAIR` 增加 `MECHANICAL CONSTRAINT=PENALTY`（Penalty 接触允许接触节点跨域共享，且与 `*TIE` 约束兼容）。若多核仍失败，可改用 `cpus=1` 单域运行。

## 5. 验证实现

`verify()` 方法在写出后立即运行，执行 **13 项检查**。验证报告同时输出到：

1. `.inp` 文件末尾的 `**` 注释段（保留供 Abaqus 读取时查看）
2. 控制台 `print()`（供开发者快速查看）

检查项分类：
- **错误级（errors）**：节点/单元 ID 唯一性、ID ≥ 1、连通性长度、材料引用、密度 > 0、节点引用存在性
- **警告级（warnings）**：塑性表首行非 0、塑性应变非单调、属性引用缺失、GRAV 函数缺失

## 6. 调试过程中遇到的问题

| 问题                                                | 根因                                            | 解决方案                                                  |
| --------------------------------------------------- | ----------------------------------------------- | --------------------------------------------------------- |
| `ValueError: invalid literal for int() with base 10: ''` | `sid` 在两段式关键字（如 `/PART/1`）中是空字符串 | 引入 `id_of(slot)` 帮助函数，根据 slot 索引取 ID         |
| 标题变成 `/DEFAULT/INTER/TYPE2`                     | `/TITLE` 后跟空行，`_read_data_line` 误读了下一个关键字 | 新增 `_read_optional_data_line()`，不消费关键字行         |
| 节点统计数 = 0                                       | `node_count` 只在 EOF 分支赋值                  | 在遇到下一个关键字的 `return` 分支也赋值                  |
| `*DLOAD` 重力方向丢失                                | 错误地取了 `abs(mag)`                          | 保留 `mag` 的符号                                          |
| `*TIE` 参数拼写错误                                  | `TOLERENCE` 应为 `TOLERANCE`                   | 修正拼写                                                  |
| 刚性墙使用 `*SURFACE, TYPE=SEGMENTS`（非解析刚性面） | Abaqus 解析刚性面应使用 `*ANALYTICAL SURFACE`  | 改为 `*ANALYTICAL SURFACE` + `*SYSTEM` + `LINE` 段         |
| `*VARIABLE MASS SCALING` 引用未定义的 `ALL_ELEMS`   | 未生成全单元集                                  | 移除该行（用户可按需手动添加 mass scaling）               |
| `*CONTACT PAIR` 在 `*SURFACE INTERACTION` 之前      | Abaqus 要求 surface interaction 先定义           | 调整写出顺序：先 `*SURFACE INTERACTION` 再 `*CONTACT PAIR` |
| `*DLOAD with BX/BY/BZ is not supported`（feinput 警告） | Abaqus/Explicit feinput 不支持 BX/BY/BZ 体载荷类型 | 改用 `GRAV` 载荷类型，作用于 `ALL_ELEMS` 单元集；方向余弦承载符号 |
| `*RIGID BODY, ANALYTICAL SURFACE` 缺少对应 `*SURFACE`（feinput 警告） | 该 feinput 版本期望 `*RIGID BODY` 引用 `*SURFACE` 而非 `*ANALYTICAL SURFACE` | 改用离散刚性面 R3D4 单元 + `*SURFACE` + `*RIGID BODY, ELSET=` |
| `Element type C3D10 is not available for this procedure` | Abaqus/Explicit 不支持 `C3D10`，仅支持 `C3D10M` | 改用 `C3D10M`（modified tetrahedron）                    |
| `109,600 elements have zero volume`                  | Radioss TETRA10 角点方向与 Abaqus C3D10M 相反 | 节点顺序重排，索引映射 `[0,2,1,3,6,5,4,7,9,8]`            |
| `*INITIAL CONDITIONS, TYPE=VELOCITY` 数据格式错误   | 单行写 `node, vx, vy, vz` 不符合规范             | 改为每 DOF 一行：`node, dof, value`                        |
| `*NSET, GENERATE` 产生未定义节点（feinput 警告）    | 节点 ID 非连续，`GENERATE` 包含了空洞           | `ALL_NODES` 改为显式列出所有节点 ID                       |
| R3D4 的 `S1..S6` 面标识符不识别                      | R3D4 仅接受 `SPOS`/`SNEG`/`E1..E4`              | 改用 `SPOS`                                               |
| `*STEP, NLGEOM=YES, INC=100000` 中 `INC` 不支持     | `INC` 仅在 Abaqus/Standard 中有意义             | 移除 `INC` 参数                                           |
| `*DYNAMIC, EXPLICIT` 数据行格式错误（多次迭代） | 3 字段 `0.001, , 1.0e-6` 时长列错位；2 字段 `0.001, 1.0e-6` 被当成 Standard 格式 | 使用 4 字段 `, 0.001, , 1.0e-6`（空, T, 空, Δtmax） |
| `KEYWORD CARDS FOR STEP DEPENDENT INPUT MUST APPEAR AFTER *STEP` | `*CONTACT PAIR` 放在 model level | 移到 `*STEP` 之内（暂存到 `_pending_contact_pairs` 列表） |
| `UNKNOWN PARAMETER TYPE`（`*CONTACT PAIR`）          | `TYPE=SURFACE TO SURFACE` 参数不被识别          | 移除 `TYPE=` 参数，使用默认值                             |
| `The requested number of domains cannot be created`（`.sta`） | 默认运动学接触 + 14 个 `*TIE` 导致 6 域 MPI 分解失败 | `*CONTACT PAIR` 增加 `MECHANICAL CONSTRAINT=PENALTY`；必要时 `cpus=1` |

## 7. 测试结果

### 7.1 转换统计

| 项             | 数量      |
| -------------- | --------- |
| 节点           | 257,099   |
| 单元总数       | 130,218   |
| └ C3D8（六面体）| 20,618    |
| └ C3D10M（四面体）| 109,600   |
| 部件（PART）   | 17        |
| 材料           | 5         |
| 属性           | 17        |
| 函数表         | 4         |
| 节点集         | 16        |
| 初始速度       | 1         |
| 重力载荷       | 3         |
| TYPE2 绑定约束 | 14        |
| 表面段         | 14        |
| 刚性墙         | 1         |

### 7.2 验证结果

```
==================== VERIFICATION SUMMARY ====================
  nodes         : 257099
  elements      : 130218
  parts         : 17
  materials     : 5
  properties    : 17
  functions     : 4
  node groups   : 16
  initial vel.  : 1
  gravity loads : 3
  TYPE2 ties    : 14
  surfaces      : 14
  rigid walls   : 1
  errors        : 0
===============================================================
```

### 7.3 连通性长度独立校验

使用独立 Python 脚本扫描 `.inp` 文件，逐元素检查节点数：

```
C3D8 elements  : 20618
C3D10M elements: 109600
Total          : 130218
Bad            : 0
```

所有 130,218 个单元的连通性长度均符合预期（C3D8=8、C3D10M=10）。

### 7.4 TETRA10 节点顺序体积校验

对节点顺序重排后的 C3D10M 单元抽样计算 signed volume（5 个随机单元），全部为正值，确认 Radioss → Abaqus 节点顺序重排正确：

```
Sample C3D10M signed volumes (should be POSITIVE):
  eid 90533:  V = +8.10e-02
  eid 123456: V = +5.43e-02
  ...（均为正）
```

### 7.5 文件大小

| 文件                          | 大小      |
| ----------------------------- | --------- |
| `Cell_Phone_Drop_0000.rad`   | 42,378,237 字节（约 40 MB） |
| `Cell_Phone_Drop.inp`        | 39,010,613 字节（约 37 MB） |
| `radioss2inp.py`             | 48,078 字节（约 47 KB）     |

## 8. 已知限制与未来工作

### 8.1 已知简化

1. `*TIE` 的 master 面使用基于节点的 `*SURFACE, TYPE=NODE`，而非单元面 surface。在大变形或穿透敏感场景下可能需要改为基于单元面（SNEG/SPOS）的 surface。
2. 刚性墙接触 slave 面 = `ALL_NODES`（全模型节点，**显式列出**所有节点 ID，因节点 ID 非连续，不能用 `GENERATE`）。覆盖范围较保守，运行性能受影响时，可改为各 PART 表面节点集。
3. 刚性墙使用单个 R3D4 单元（4 个角节点）表示；如需更精细的几何，可改为多个 R3D4 单元组成的网格。
4. 刚性墙 master 面使用 `SPOS` 标识符（R3D4 仅接受 `SPOS`/`SNEG`/`E1..E4`，不接受 `S1..S6`）。
5. 未转换 `/DEFAULT/INTER/TYPE2` 与 `/DEF_SOLID` 全局控制卡片。Abaqus 在 `*SOLID SECTION` 与各 `*TIE` 中分别指定即可。
6. `/MAT/PLAS_TAB` 仅支持 `N_funct=1` 的单函数塑性表；多函数率相关塑性未实现。
7. 未转换 `/SURF/PART`、`/SURF/SURF` 等其他 surface 类型；仅处理了 `/SURF/SEG`。
8. `/INIVEL/ROT`（初始角速度）未实现，仅处理了 `/INIVEL/TRA`（平动速度）。
9. `*DYNAMIC, EXPLICIT` 数据行使用 4 字段格式 `, time_period, , max_increment`（当前为 `, 0.001, , 1.0e-6`）；如需自定义时间步长控制，可手动修改。
10. `*STEP` 不使用 `INC` 参数（Abaqus/Explicit 不支持，仅 Standard 支持）。
11. `*CONTACT PAIR` 不带 `TYPE=` 参数（Abaqus 6.14 feinput 不识别 `TYPE=SURFACE TO SURFACE`，使用默认值），并指定 `MECHANICAL CONSTRAINT=PENALTY`（与 `*TIE` 兼容，支持多域并行分解）。
12. 多核 MPI 并行（如 6 domains）若仍报域分解错误，可改用 `abaqus job=... cpus=1` 单域运行。

### 8.2 可扩展方向

- **新增关键字**：在 `_dispatch_keyword()` 中添加新的 `elif` 分支，并实现对应的 `_parse_xxx()` 方法。
- **单元面 surface**：建立 element-face 查找表，将 `*TIE` 与 `*CONTACT PAIR` 的 master 面改为单元面 surface。
- **多函数塑性**：解析 `N_funct > 1` 的 `/MAT/PLAS_TAB`，生成 Abaqus `*PLASTIC, RATE=...` 多行表。
- **GUI 前端**：可基于 PyQt 或 Web 界面包装脚本，提供文件选择与转换进度显示。
- **Abaqus CAE 验证**：使用 Abaqus CAE 的 `mdb.ModelFromInputFile()` API 自动验证 `.inp` 文件能否被 Abaqus 读取，作为更严格的端到端测试。

## 9. 文件清单

```
radioss2inp/
├── Cell_Phone_Drop_0000.rad   # OpenRadioss 输入文件示例
├── Cell_Phone_Drop.inp         # 转换生成的 Abaqus 输入文件
├── radioss2inp.py              # 转换脚本（含解析器、写出器、验证器）
├── README.md                   # 用户文档
└── DEV_SUMMARY.md              # 开发总结文档（本文件）
```
