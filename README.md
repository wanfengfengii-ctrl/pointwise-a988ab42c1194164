# 澄河观测湖

澄河水文资料中心的台站提交具名原批。归档班核对观测和编码出处后签发日批，质控班在独立镜像中逐批签收，使用回执和当下可读仓态核账。工程使用 Python 3.11 标准库和 SQLite。

## 当天业务

1. 归档员登记或按号读取当日基准，核对当前有效出处和既有观测。
2. 台站提交带稳定原批号的出处与观测改写。
3. 归档员核对重投，确认原批号仍指向同一份内容。
4. 日批窗口关闭，归档员选择同一基准下的一至八份已登记原批。
5. 发布器寻找一条能完整兑现的顺序，每个中间仓态都必须能解释其中的所有观测。
6. 归档员按交付号导出固定件。质控班可脱离源库接收。
7. 质控班在安装了相同基准的镜像上提交下一原批。
8. 质控员按号查回执，读取当前已提交前缀的出处、观测和位置。
9. 交班重开镜像后，先核对重投回执，再继续接收余批。

## 运行

在仓库根目录用 Python 3.11 运行。

```sh
python -B -m unittest discover -s tests -v
python -B -m lake_checks demo
python -B -m lake_checks incident
```

演示在临时目录中建立独立的源库和镜像库。源库关闭后，镜像按固定件继续签收。演示输出实际回执前缀，并核对手算结果。换接样例使用旧出处 `old-X` 和原批 `A`、`B`、`C`。B 签收时只有其 44 毫米观测可读，旧 X 仍有效。C 签收时其 17 毫米观测出现且两个 X 出处并存。A 签收时旧出处停用。`samples/interdependent.json` 使用空基准，B 和 C 互相依赖且都无法先成为有效原批。

## Python 入口

把 SQLite 文件连接传给公开入口。初始化、发布、接收和一致快照读取管理自己的事务，调用时连接应处于空闲状态。库可独立关闭、重开。调用方负责连接生命周期。

| 职责 | 入口与结果 |
| --- | --- |
| 源库初始化 | `sources.bootstrap.initialize_source(db, baseline_id, snapshot, batches=())` 原子安装基准和可选样例原批，返回基准号 |
| 基准登记与读取 | `warehouse.baselines.register_baseline(db, baseline_id, snapshot)` 返回基准号，`read_baseline(db, baseline_id)` 返回 `WarehouseSnapshot` |
| 原批登记与读取 | `sources.batches.register_batch(db, batch)` 返回原批号，`read_batch(db, batch_id)` 返回 `OriginalBatch` |
| 日批发布 | `delivery.registry.publish_delivery(db, baseline_id, batch_ids)` 返回交付号，`read_delivery(db, delivery_id)` 返回固定清单 |
| 离线导出 | `delivery.export.export_delivery(db, delivery_id)` 返回 JSON 文本 |
| 镜像初始化 | `mirror.state.initialize_mirror(db, baseline_id, snapshot)` 返回持久游标 |
| 原批签收 | `mirror.apply.accept_batch(db, package, batch_id)` 返回 `Receipt` |
| 质控仓态 | `mirror.read.read_mirror(db)` 返回同一读快照下的游标、仓态标识、出处和观测 |
| 回执核账 | `receipts.lookup.read_receipt(db, receipt_id)` 返回原交付号、原批号和原位置 |

`warehouse.snapshots.make_snapshot` 接收 `EffectiveSource(source_id, code)` 和 `Observation(observation_id, code, value)`。`sources.records.OriginalBatch` 接收批号和有序改写元组。改写类型为 `SourceChange(source_id, operation, code=None)` 或 `ObservationChange(observation_id, operation, code=None, value=None)`。`upsert` 写入或更正身份对应的记录，`remove` 按身份撤销且不携带其他字段。移除已缺席的身份保持原状。同一原批可依次改写同一身份。空改写元组保留原批身份并产生一个签收位置。标识采用非空白 Unicode 文本，身份按原文本比较。观测使用 Python 整数，线格式和 SQLite 投影使用规范带符号十六进制文本保留精度。空值、布尔值及缺失的必需字段无效。JSON 对象的字段顺序统一规范化，改写数组顺序参与内容绑定。

仓态校验以完整原批为边界。多条有效出处可以提供同一编码，停用一条只移除对应身份。跨原批重复改写同域同身份的轮次会拒发。空基准可登记，空轮会拒发。存在多条合法顺序时，选择按批号探索得到的首个完整顺序。离线交付的内容摘要核对基准号、基准内容和有序原批，也能识别意外改写。交付件由归档班提供，摘要用于内容绑定。

源库同号同文登记稳定返回原号。`InvalidBatch`、`BatchConflict` 和 `BaselineConflict` 说明输入或既有绑定有冲突，未知键分别报告 `BatchNotFound`、`BaselineNotFound` 或 `DeliveryNotFound`。轮次选择错误报告 `InvalidSelection`，无可行全序报告 `NoLegalOrder`，发布失败无新增交付。重复初始化同一基准保留已有记录。装入样例时遇同号异文会回滚整个安装请求并保留原数据。

镜像以安装基准为位置 0，同一固定交付的原批从 1 开始签收。错配内容、错基准、错交付、未知成员、跳批或无出处批分别报告 `InvalidManifest`、`DeliveryBindingMismatch`、`BatchOutsideDelivery`、`BatchOutOfOrder` 或 `MissingEffectiveSource`。未安装基准时读写报告 `MirrorNotReady`，未知回执报告 `ReceiptNotFound`。镜像在一个事务提交湖表、交付绑定、位置和回执，失败保持原状态。相同基准重复安装保留已有前缀，异文初始化报告 `MirrorInitializationConflict`。已签收原批重投返回原回执，即使当前镜像已收到后续批次。镜像查询提供当前前缀，历史回执保留当时位置。每个镜像实例接受一份固定交付。
