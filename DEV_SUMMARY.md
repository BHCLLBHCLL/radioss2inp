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

按 Abaqus 6.14 标准顺序输出：

1. `*HEADING` + `*PREPRINT`
2. `*NODE`
3. 每个 PART：`*ELEMENT` + `*SOLID SECTION`
4. 所有材料：`*MATERIAL` + `*ELASTIC` + `*PLASTIC` + `*DENSITY`
5. 所有 `*NSET`、`*AMPLITUDE`
6. `*INITIAL CONDITIONS, TYPE=VELOCITY`
7. `*DLOAD`（体加速度）
8. `*TIE` 约束
9. 刚性墙：`*ANALYTICAL SURFACE` + `*RIGID BODY` + `*CONTACT PAIR`
10. `*STEP` + `*DYNAMIC, EXPLICIT` + 输出请求
11. 验证报告（以 `**` 注释形式附加）

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

### 4.2 TETRA10 双行记录解析

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

### 4.3 `/TITLE` 的空数据行处理

源文件的 `/TITLE` 后跟一个空行，直接用 `_read_data_line()` 会错误地把下一个关键字（`/DEFAULT/INTER/TYPE2`）当作标题。解决方案：新增 `_read_optional_data_line()`，遇到 `/` 开头的行时不消费，直接 `return None` 并通过 `_dispatch_keyword()` 处理该关键字。

### 4.4 `/GRAV` 的方向符号保留

Radioss 中 GRAV 的 `Fscale` 是带符号的加速度。Abaqus `*DLOAD` 的 `BX/BY/BZ` 同样支持符号（正负代表方向），转换时**必须保留** `Fscale` 的符号，否则重力方向会反转。早期实现错误地取了绝对值，已修正。

### 4.5 刚性墙几何重建

`/RWALL/PLANE` 只给出平面上的两个点 M 和 M1（M→M1 即法向）。转换为 Abaqus `*ANALYTICAL SURFACE` 时需要：

1. 计算法向单位向量 `n = (M1 - M) / |M1 - M|`
2. 构造平面内两个正交向量 `u`、`v`（用 Gram-Schmidt 从 `(ny, -nx, 0)` 或 `(0, nz, -ny)` 出发）
3. 用 `*SYSTEM` 定义局部坐标系（原点=M，x=u，y=v）
4. 在局部坐标系下输出 4 段 `LINE` 形成矩形平面（±L=200 mm）

### 4.6 命名安全化 `_safe_name()`

Radioss 材料名包含空格和特殊字符（如 `polymer_unfilled_plastic`、`Table  8`），Abaqus 要求名称必须字母开头、仅含字母/数字/下划线、长度 ≤ 80。函数实现：

```python
cleaned = re.sub(r'[^A-Za-z0-9_]', '_', name.strip())
cleaned = re.sub(r'_+', '_', cleaned).strip('_')
if not cleaned[0].isalpha():
    cleaned = (prefix or 'X') + '_' + cleaned
return cleaned[:80]
```

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
| `*DLOAD` 重力方向丢失                                | 错误地取了 `abs(mag)`                          | 保留 `mag` 的符号                                         |
| `*TIE` 参数拼写错误                                  | `TOLERENCE` 应为 `TOLERANCE`                   | 修正拼写                                                  |
| 刚性墙使用 `*SURFACE, TYPE=SEGMENTS`（非解析刚性面） | Abaqus 解析刚性面应使用 `*ANALYTICAL SURFACE`  | 改为 `*ANALYTICAL SURFACE` + `*SYSTEM` + `LINE` 段         |
| `*VARIABLE MASS SCALING` 引用未定义的 `ALL_ELEMS`   | 未生成全单元集                                  | 移除该行（用户可按需手动添加 mass scaling）               |
| `*CONTACT PAIR` 在 `*SURFACE INTERACTION` 之前      | Abaqus 要求 surface interaction 先定义           | 调整写出顺序：先 `*SURFACE INTERACTION` 再 `*CONTACT PAIR` |

## 7. 测试结果

### 7.1 转换统计

| 项             | 数量      |
| -------------- | --------- |
| 节点           | 257,099   |
| 单元总数       | 130,218   |
| └ C3D8（六面体）| 20,618    |
| └ C3D10（四面体）| 109,600   |
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
C3D8 elements : 20618
C3D10 elements: 109600
Total         : 130218
Bad           : 0
```

所有 130,218 个单元的连通性长度均符合预期（C3D8=8、C3D10=10）。

### 7.4 文件大小

| 文件                          | 大小      |
| ----------------------------- | --------- |
| `Cell_Phone_Drop_0000.rad`   | 42,378,237 字节（约 40 MB） |
| `Cell_Phone_Drop.inp`        | 39,010,613 字节（约 37 MB） |
| `radioss2inp.py`             | 48,078 字节（约 47 KB）     |

## 8. 已知限制与未来工作

### 8.1 已知简化

1. `*TIE` 的 master 面使用基于节点的 `*SURFACE, TYPE=NODE`，而非单元面 surface。在大变形或穿透敏感场景下可能需要改为基于单元面（SNEG/SPOS）的 surface。
2. 刚性墙接触 slave 面 = `ALL_NODES`（全模型节点），覆盖范围较保守。运行性能受影响时，可改为各 PART 表面节点集。
3. 未转换 `/DEFAULT/INTER/TYPE2` 与 `/DEF_SOLID` 全局控制卡片。Abaqus 在 `*SOLID SECTION` 与各 `*TIE` 中分别指定即可。
4. `/MAT/PLAS_TAB` 仅支持 `N_funct=1` 的单函数塑性表；多函数率相关塑性未实现。
5. 未转换 `/SURF/PART`、`/SURF/SURF` 等其他 surface 类型；仅处理了 `/SURF/SEG`。
6. `/INIVEL/ROT`（初始角速度）未实现，仅处理了 `/INIVEL/TRA`（平动速度）。

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
