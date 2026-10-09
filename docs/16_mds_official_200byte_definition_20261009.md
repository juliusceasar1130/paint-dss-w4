# 官方 MDS 200 字节数据块定义（Payload 字段权威基准）

> **来源**：业务侧提供（车间 MES / PLC 官方 DB 块定义，EcoEMOS GUI 导出格式），2026-10-09 接收
> **地位**：本项目 MDS 电报的**官方原始定义（200 字节 payload）**；2026-09 时代逆向俗称（vehicle_id / black_roof_flag / reserved_1/2 等，原 `docs/08_message_data_dictionary.md` §2 继承）与本文档冲突时以本文档为准
> **范围**：纯 Payload，Byte 0..199，共 **200 字节**、**51 个字段**（其中 Byte 118..119 为 16 位部件布尔块）
> **原文存档**：`scratch/mds_official_200byte_raw.tsv`（verbatim 保留，含换行/引号原始格式）
> **PG 映射**：本 200 字节 payload 在 PG 237 字符电报中的映射（位序 + 实测值域 + 待确认清单）见 `docs/18_MDS_DATA_237字符电报_PG映射参考.md`（开发依据，2026-10-09 全量定稿）
> **相关**：`output/mds_data_definition_audit_report_20261009.md`（审计报告 v1.1，审计过程工件）、`output/mds_audit_report_recheck_20261009.md`（独立复核报告 v2，映射裁决证据）、`docs/17_滚床30字符实时报文字典.md`（滚床 30 字符报文，跨源互证）
> **文档拆分史**：2026-10-09 ① 原 `docs/08_message_data_dictionary.md` 的 MDS 部分并入本文档，该文件更名 `docs/17_滚床30字符实时报文字典.md`（只留滚床报文）；② 本文档的 PG 映射部分拆出为 `docs/18_MDS_DATA_237字符电报_PG映射参考.md`，本文件回归官方 200 字节定义

---

## 0. 结构自检（2026-10-10 脚本核验，`scratch/tmp_mds_official_check.py`）

| 检查项 | 结果 |
|---|---|
| 字节连续性（0..199 无断档/无重叠） | 通过 |
| 各字段 Length = End − Start + 1 | 通过（51/51） |
| 总长 | 200 字节（首字段 Start=0，末字段 End=199） |
| 字段数 | 51 |
| 类型分布 | Char 42（150 字节）、DINT 8（32 字节）、Bool 块 1（2 字节 = 16 bit） |
| 118..119 Bool 块 Bit 数 | 16（Bit 0..15，见 §2） |
| 与审计报告 §4（v1.1 定稿）逐行 PG 映射比对 | 51/51 一致（映射模型见 `docs/18` §1；**2026-10-09 v2 修正**：后半段 64 字节→65 字符、SkidDest 5 位序列化、无独立尾字段） |

## 1. 字段定义表（按官方原文解析）

| # | Start Byte | End Byte | Length | Data type | Name of data field | Text GUI EcoEMOS |
|---:|---:|---:|---:|---|---|---|
| 1 | 0 | 13 | 14 | Char | BodyId | Body Number |
| 2 | 14 | 19 | 6 | Char | BodyType | Body Type |
| 3 | 20 | 23 | 4 | Char | Platform | Platform |
| 4 | 24 | 27 | 4 | Char | SerialNumber | Serial Number |
| 5 | 28 | 31 | 4 | Char | TopCoatColor | Top Coat Color |
| 6 | 32 | 35 | 4 | Char | LineInfo | Line Info |
| 7 | 36 | 39 | 4 | Char | MotorType | Motor Type |
| 8 | 40 | 43 | 4 | Char | SteeringWheel | Steering Wheel |
| 9 | 44 | 47 | 4 | Char | CoverType | Cover type |
| 10 | 48 | 51 | 4 | Char | Sunroof | Sunroof |
| 11 | 52 | 57 | 6 | Char | EngineModel | Engine model |
| 12 | 58 | 59 | 2 | Char | InteriorTrim | InteriorTrim |
| 13 | 60 | 89 | 30 | Char | FIS-Reserve | FIS-Reserve |
| 14 | 90 | 97 | 8 | Char | APT_ColorCode1 | APT_ColorCode1 |
| 15 | 98 | 105 | 8 | Char | APT_ColorCode2 | APT_ColorCode2 |
| 16 | 106 | 106 | 1 | Char | CodeF | CodeF |
| 17 | 107 | 107 | 1 | Char | CodeA | CodeA |
| 18 | 108 | 108 | 1 | Char | CodeB | CodeB |
| 19 | 109 | 109 | 1 | Char | KVS Flag | KVS Flag |
| 20 | 110 | 110 | 1 | Char | TypeComparisionState | TypeComparisionState |
| 21 | 111 | 111 | 1 | Char | EDCoatingProduction State | EDCoatingProduction State |
| 22 | 112 | 112 | 1 | Char | RoofCheckingProduction State | RoofCheckingProduction State |
| 23 | 113 | 113 | 1 | Char | UBSProductionState | UBSProductionState |
| 24 | 114 | 114 | 1 | Char | TopCoatProductionState | TopCoatProductionState |
| 25 | 115 | 115 | 1 | Char | PaintCheckerProduction State | PaintCheckerProduction State |
| 26 | 116 | 116 | 1 | Char | SchwellerProductionState | SchwellerProductionState |
| 27 | 117 | 117 | 1 | Char | AuditCheck | AuditCheck |
| 28 | 118 | 119 | 2 | Bool（16 bit） | 见 §2 | 见 §2 |
| 29 | 120 | 120 | 1 | Char | NoPaint | No Paint |
| 30 | 121 | 121 | 1 | Char | SecondRun | Second Run |
| 31 | 122 | 122 | 1 | Char | OrderType | Order Type |
| 32 | 123 | 123 | 1 | Char | Spare | Spare |
| 33 | 124 | 127 | 4 | DINT | ArrivalDate | Arrival Date |
| 34 | 128 | 131 | 4 | DINT | ArrivalTime | Arrival Time |
| 35 | 132 | 135 | 4 | DINT | CarrierID | Carrier ID |
| 36 | 136 | 149 | 14 | Char | TIMESTAMP | TIMESTAMP |
| 37 | 150 | 150 | 1 | Char | PTLine | PT Line |
| 38 | 151 | 151 | 1 | Char | PVCLine | PVC Line |
| 39 | 152 | 153 | 2 | Char | EDOvenLine | ED Oven Line |
| 40 | 154 | 155 | 2 | Char | TCPreLine | TCPreparation Line |
| 41 | 156 | 156 | 1 | Char | TCLine | TC Line |
| 42 | 157 | 157 | 1 | Char | TCLineBlockRoof | TCLine BlockRoof |
| 43 | 158 | 159 | 2 | Char | skidType | Skid Type |
| 44 | 160 | 163 | 4 | DINT | skidId | Skid Number |
| 45 | 164 | 167 | 4 | DINT | CleaningCycleCounter | Cleaning Cycle Counter |
| 46 | 168 | 171 | 4 | DINT | LifeCycleCounter | Life Cycle Counter |
| 47 | 172 | 175 | 4 | DINT | SkidDestination | Skid Destination |
| 48 | 176 | 176 | 1 | Char | Identification Damaged Skid | Identification Damaged Skid |
| 49 | 177 | 177 | 1 | Char | WriteOperation | Write Operation |
| 50 | 178 | 181 | 4 | DINT | TabletDestination | Tablet Destination |
| 51 | 182 | 199 | 18 | Char | Reserve2 | Reserve2 |

**备注**：
- 官方原文中 118..119 行的 Data type 列写作 `Bool: 0`，各 bit 名称在后续行给出（§2）；该行在审计报告 §4.1 中被命名为 `PartFlags (16 Bit)`（报告自拟名，官方无此字段名）。
- DINT 共 8 个（ArrivalDate / ArrivalTime / CarrierID / skidId / CleaningCycleCounter / LifeCycleCounter / SkidDestination / TabletDestination），每个 4 字节。
- 官方定义**只给字段名与 GUI 文案，未给取值语义**（如 CodeF/A/B 的取值含义、KVS Flag 置 1 的条件、skidType 各字母含义）——语义层面的推断只能来自数据实测，见审计报告复核。

## 2. Byte 118..119 的 16 Bit 定义（官方原文逐 Bit）

| Bit | Name of data field | Text GUI EcoEMOS |
|---:|---|---|
| 0 | FrontFenderLe | Front Fender Left |
| 1 | FrontDoorLe | Front Door Left |
| 2 | RearDoorLe | Rear Door Left |
| 3 | RearSideLe | Rear Side Left |
| 4 | MotorHood | Motor Hood |
| 5 | Roof | Roof |
| 6 | TrunkLid | Trunk Lid |
| 7 | FrontFenderRi | Front Fender Right |
| 8 | FrontDoorRi | Front Door Right |
| 9 | RearDoorRi | Rear Door Right |
| 10 | RearSideRi | Rear Side Right |
| 11 | Reserve | Reserve |
| 12 | Reserve | Reserve |
| 13 | Reserve | Reserve |
| 14 | （无字段名） | "Control Character - not usable for payload" |
| 15 | （无字段名） | （原文为空） |

**口径**：有部件语义的 bit 只有 **11 个**（Bit 0..10：左前翼子板、左前门、左后门、左后侧围、机盖、车顶、尾门、右前翼子板、右前门、右后门、右后侧围）；Bit 11..13 为预留，Bit 14 为控制字符（不可用于 payload），Bit 15 无名。因此「16 个车身部件布尔位」的说法不严谨，准确表述是「16 位部件标志块（11 个命名部件位 + 3 预留 + 1 控制字符 + 1 无名）」。

## 3. 原文 verbatim（业务侧提供，2026-10-09）

```text
Start adress
Byte	End adress
Byte	Length [Byte]	Data type	Name of data field	Text GUI EcoEMOS
0	13	14	Char	"BodyId
"	Body Number
14	19	6	Char	"BodyType
"	Body Type
20	23	4	Char	Platform	Platform
24	27	4	Char	SerialNumber	Serial Number
28	31	4	Char	TopCoatColor	Top Coat Color
32	35	4	Char	LineInfo	Line Info
36	39	4	Char	MotorType	Motor Type
40	43	4	Char	SteeringWheel	Steering Wheel
44	47	4	Char	CoverType	Cover type
48	51	4	Char	Sunroof	Sunroof
52	57	6	Char	EngineModel	Engine model
58	59	2	Char	InteriorTrim	InteriorTrim
60	89	30	Char	FIS-Reserve	FIS-Reserve
90	97	8	Char	APT_ColorCode1	APT_ColorCode1
98	105	8	Char	APT_ColorCode2	APT_ColorCode2
106	106	1	Char	CodeF	CodeF
107	107	1	Char	CodeA	CodeA
108	108	1	Char	CodeB	CodeB
109	109	1	Char	KVS Flag	KVS Flag
110	110	1	Char	TypeComparisionState	TypeComparisionState
111	111	1	Char	EDCoatingProduction State	EDCoatingProduction State
112	112	1	Char	RoofCheckingProduction State	RoofCheckingProduction State
113	113	1	Char	UBSProductionState	UBSProductionState
114	114	1	Char	TopCoatProductionState	TopCoatProductionState
115	115	1	Char	PaintCheckerProduction State	PaintCheckerProduction State
116	116	1	Char	SchwellerProductionState	SchwellerProductionState
117	117	1	Char	AuditCheck	AuditCheck
118	119	2	Bool: 0	FrontFenderLe	Front Fender Left
	Bool: 1	FrontDoorLe	Front Door Left
	Bool: 2	RearDoorLe	Rear Door Left
	Bool: 3	RearSideLe	Rear Side Left
	Bool: 4	MotorHood	Motor Hood
	Bool: 5	Roof	Roof
	Bool: 6	TrunkLid	Trunk Lid
	Bool: 7	FrontFenderRi	Front Fender Right
	Bool: 8	FrontDoorRi	Front Door Right
	Bool: 9	RearDoorRi	Rear Door Right
	Bool: 10	RearSideRi	Rear Side Right
	Bool: 11	Reserve	Reserve
	Bool: 12	Reserve	Reserve
	Bool: 13	Reserve	Reserve
	Bool: 14	"Control Character-
not usable for payload"	
	Bool: 15		
120	120	1	Char	NoPaint	No Paint
121	121	1	Char	SecondRun	Second Run
122	122	1	Char	OrderType	Order Type
123	123	1	Char	Spare	Spare
124	127	4	DINT	ArrivalDate	Arrival Date
128	131	4	DINT	ArrivalTime	Arrival Time
132	135	4	DINT	CarrierID	Carrier ID
136	149	14	Char	TIMESTAMP	TIMESTAMP
150	150	1	Char	PTLine	PT Line
151	151	1	Char	PVCLine	PVC Line
152	153	2	Char	EDOvenLine	ED Oven Line
154	155	2	Char	TCPreLine	TCPreparation Line
156	156	1	Char	TCLine	TC Line
157	157	1	Char	TCLineBlockRoof	TCLine BlockRoof
158	159	2	Char	skidType	Skid Type
160	163	4	DINT	skidId	Skid Number
164	167	4	DINT	CleaningCycleCounter	Cleaning Cycle Counter
168	171	4	DINT	LifeCycleCounter	Life Cycle Counter
172	175	4	DINT	SkidDestination	Skid Destination
176	176	1	Char	Identification Damaged Skid	Identification Damaged Skid
177	177	1	Char	WriteOperation	Write Operation
178	181	4	DINT	TabletDestination	Tablet Destination
182	199	18	Char	Reserve2	Reserve2
```


---

> **PG 237 字符电报映射**：本 200 字节 payload 在 PG 237 字符电报中的映射（位序、实测值域、25 项待用户确认清单）见 `docs/18_MDS_DATA_237字符电报_PG映射参考.md`（开发依据，2026-10-09 全量定稿）。
