# 知构 UML 图数据约定 v1

画布保存格式由前端2、后端1与 Agent 工程共同使用。业务接口只保存结构与版本，并验证 ID 和端点引用；UML 语义、自动布局、图代码一致性检查由相应同学实现。

## 保存示例

新建图 POST `/api/v1/diagrams`，成功产生 `diagram_id` 与 `version=1`。

```json
{
  "title": "失物招领类图",
  "diagram_type": "class",
  "data": {
    "nodes": [
      {"id": "class-student", "type": "class", "label": "Student", "position": {"x": 100, "y": 100}, "properties": {"attributes": [{"name": "studentId", "type": "String", "visibility": "private"}], "methods": [{"name": "claim", "returnType": "void"}]}},
      {"id": "class-item", "type": "class", "label": "LostItem", "position": {"x": 420, "y": 100}, "properties": {"attributes": [], "methods": []}}
    ],
    "edges": [
      {"id": "edge-claim", "source": "class-student", "target": "class-item", "type": "association", "label": "认领", "properties": {"sourceMultiplicity": "1", "targetMultiplicity": "0..*"}}
    ],
    "metadata": {"schema_version": "1.0", "viewport": {"x": 0, "y": 0, "zoom": 1}}
  }
}
```

后续 PUT `/api/v1/diagrams/{diagram_id}` 发送同样字段并增加 `base_version`。旧版本不会被覆盖。收到 `409 VERSION_CONFLICT` 时先提示用户并拉取最新版本，合并后再提交，不能自动用过期画布覆盖新内容。恢复历史版本时读取 `?version=1`，再以当前最新 `base_version` 提交该图数据，形成新版本。

## 字段和边界

| 字段 | 约定 |
| --- | --- |
| `diagram_type` | `class`、`use_case`、`sequence`，创建后不能改；换类型请新建 |
| `nodes` | 最多500个；每节点需要 id/type/label/position；properties 默认为空对象 |
| `edges` | 最多1000条；需要 id/source/target/type；label默认为空串，properties默认为空对象 |
| `position` | x/y 为有限数值，保存画布坐标，可为负数 |
| `properties` | 领域属性扩展区，节点尺寸、方法、颜色、关联方向、消息顺序等放这里 |
| `metadata` | 可选对象；前端视口、schema_version、编辑器版本等放这里 |
| 节点和边 ID | 同一版本内全局唯一；每个边端点都必须引用本图存在的节点 |
| 数据量 | `data` 序列化后不得超过2MiB；无效图返回422 |

推荐节点类型：类图用 `class/interface`；用例图用 `actor/use_case/system_boundary`；时序图用 `lifeline/activation/note`。推荐连线类型：类图 `inheritance/implementation/association/dependency`，用例图 `association/include/extend`，时序图 `message/return`。这些语义值目前是约定，服务端不强制验证 UML 语法。系统边界归属可存 `properties.parent_id`，时序顺序可存 `edge.properties.order`；前端必须显式保存，后端不会推断。

问题高亮建议用节点 `properties.validation={severity,message,source}`。Agent返回的问题应绑定节点或边 ID，避免仅靠中文名称定位。PNG/SVG 导出由前端2处理，后端版本接口返回可编辑 JSON。现有知识库6张PNG不等同于该JSON；演示JSON另行编写于 `backend/data/demo_diagrams.json`。
