# 校园系统数据库详细设计（学生表）

## 1. 学生信息表 (Student)
用于存储所有在校学生的基本信息。

| 字段名 | 数据类型 | 允许空 | 备注说明 |
|---|---|---|---|
| stu_name | Varchar(50) | 否 | 学生姓名 |
| stu_age | Int | 是 | 学生年龄 |
| stu_major | Varchar(100) | 是 | 所属专业 |
| enroll_date | Date | 是 | 入学日期 |