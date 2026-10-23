# MCMesh · 我的世界联机存档助手

> **Minecraft LAN Co-op Companion — keeps every player's character bound to its owner, even after the host changes.**
>
> A small, dependency-free tool that versions your whole world as a Git repository: the host `push`es
> when done, the next host `pull`s and keeps playing. Each player's name and UUID live in the repo, so
> nobody loses their inventory, position, or advancements when the host hands over.
>
> 解决的事：朋友一起玩，房主一下线大家就玩不了；以及"这次该谁开、存档怎么接"。
>
> 做法：**把整份存档当 Git 仓库来管**。玩完 `push` 传上去，下一个人 `pull` 下来接着玩。
> 每个人的名字和 UUID 也记在仓库里，所以谁进去都还是自己那个角色。

---

## 0. 你这套环境（已确认）

| 项 | 值 |
|---|---|
| 整合包 | Better MC [FABRIC] BMC3（MC **1.21.1** / Fabric **0.17.2**，333 个 mod） |
| 实例目录 | `C:\Users\lanld\Downloads\.minecraft\versions\Better MC [FABRIC] BMC3` |
| 启动器 | PCL2（`Downloads\.minecraft\PCL.ini`） |
| Java | `C:\Program Files\Java\jdk-21`（21.0.8，够用） |
| Git / Python | 2.54.0 / 3.12.7（都在） |

---

## 1. 两种模式，先想清楚

### 模式 A：`lan` — 继续"对局域网开放"（推荐先玩这个）

- 需要准备的只有：**Git**（已装）+ **GitHub 私有仓库**（下面第 2 步）。
- 玩法和你现在**一模一样**：一个人进存档 → ESC → 对局域网开放 → 别人连。
- 存档怎么交接：谁玩完谁上传，下一个开的人自动拿到最新存档。
- **房主身份**：单人存档的机制是"谁打开世界谁就是本地玩家"，1.21.1 把这个人存在 `level.dat → Data.Player` 里，
  所以换人开会接手上一个房主的身体。**本工具用"角色移植"处理掉了**：每次上传自动把当前房主角色存成
  `chars/<名字>.dat`，下次换人开局时自动把各自的角色套回去（详见第 5.2 节）。
- 另外：还是得在同一个局域网（不同 WiFi 就得内网穿透）。

### 模式 B：`server` — Fabric 专用服，轮流开服

- 需要准备的：Java 21（有）+ 一次性的服务端生成（工具自动做）+ 每台电脑给自己留 6~8G 内存。
- 所有人**平权**：身份按名字严格分开，存在 `playerdata/<uuid>.dat`，背包/坐标/权限互不干扰，没有房主特权。
- 谁想玩，谁在自己电脑上开服，其他人 `开服者IP:25565` 连进去。
- **代价**：开服机器压力比单人世界大（333 mod 不轻）；第一次要生成服务端、可能还要调防火墙。

> 建议：这两天先用 A 玩起来；觉得换人继承角色很别扭，再切 B（存档可以复用）。

---

## 2. 一次性准备（只做一次）

### 2.1 GitHub 私有仓库

1. 打开 https://github.com/new
2. **Repository name** 随便，比如 `mc-world`
3. 选 **Private**（存档不想公开）
4. **不要**勾 "Add a README file" / .gitignore / license（要一个空仓库）
5. 建好后复制 https 地址，形如 `https://github.com/你的用户名/mc-world.git`

### 2.2 把朋友拉进来

仓库页 → **Settings** → **Collaborators** → **Add people** → 输他们的 GitHub 用户名。

> 每个人必须有自己的 GitHub 账号（免费）。第一次 `push` 会弹浏览器登录，登录后会记住。
> 嫌麻烦也可以把仓库设成 Public（存档会公开给所有人看，速度反而更快）。

---

## 3. 首次配置

### 3.0 桌面上的一个按钮，十个入口

桌面只有一个图标：**`MC 联机助手`**（快捷方式，指向 `C:\Users\lanld\MCMesh\联机助手.bat`）。
双击打开就是菜单，日常只用到 1 / 2 / 3：

```
  1) 开局：拉最新存档 + 启动游戏（玩完回车自动上传）
  2) 上传存档（玩完了，或上次忘了传）
  3) 拉取存档（只想同步别人的进度，不开游戏）
  4) 查看状态
  5) 角色移植（换房主时保住自己的背包）
  6) 成员管理
  7) 体检
  8) 生成/修复服务端（专用服模式才需要）
  9) 重新配置（填游戏 ID / 仓库地址）
  0) 退出
```

`MCMesh` 文件夹里另外还留着三个一次性入口，给"不想开菜单"或者朋友用：
`开始游戏.bat`（=1）、`拉取存档.bat`（=3）、`上传存档.bat`（=2）。
它们是独立文件，随便放哪都能跑：先找自己旁边的 `mcmesh.py`，找不到就回落到
`C:\Users\<你>\MCMesh\` 和 `C:\Users\<你>\Desktop\MCMesh\`。

### 3.1 配置

双击 **`MC 联机助手`** → 选 `9) 重新配置`，或者命令行：

```bat
cd /d C:\Users\lanld\MCMesh
python mcmesh.py init
```

它会依次问：

| 问题 | 填什么 |
|---|---|
| ① 你在这个世界里的名字 | **你游戏里用的 ID**，之后别改（下面第 5 节会讲为什么） |
| ② 整合包目录 | 直接选列出来的 `Better MC [FABRIC] BMC3` |
| ③ 联机方式 | `1` = 局域网模式，`2` = 专用服模式 |
| ④ 存档文件夹名 | 现有存档名，或者起个新名字（存档为空时） |
| ⑤ 启动器 | 找到就按回车让它自动开游戏 |
| ⑥ 仓库地址 | 第 2.1 步复制的地址；还没建就先留空 |
| ⑥ 分支名 | 直接回车（`main`） |

配置完，`C:\Users\lanld\MCMesh\` 里会有：

```
MCMesh\
  mcmesh.py        主程序
  config.json      你的配置
  repo\            存档仓库的本地副本（Git 仓库）
  backups\         每次 pull 前的自动备份
  state.json       同步记录
```

其余几位朋友各自：**装 Git** → 把自己的副本放到比如 `C:\Users\他的名字\MCMesh\` → 双击 `联机助手.bat` → `9) 重新配置`，填**自己的名字**和第二个人从 GitHub 拿到的仓库地址。

---

## 4. 每天怎么用

### 模式 A（局域网）

**开局的人：**
1. 双击 **`开始游戏.bat`**（= `python mcmesh.py host`）
2. 它会先 `pull` 最新存档、备份你本地那份，然后打开 PCL
3. 进游戏 → 单人游戏 → 选存档 → **ESC → 对局域网开放**（端口填 25565）
4. 告诉朋友：`多人游戏 → 直接连接 → 你电脑的IP:25565`
5. 玩完：**先退出游戏**（让存档写盘），回到那个黑窗口按回车 → 自动上传

**只想加入的人：** 不用做任何事，等房主报 IP 就行。
想自己看看最新存档（比如独自玩玩）：双击 `联机助手.bat` → `1) 我要开局`。

### 模式 B（专用服）

**开服的人：**
1. 第一次：`python mcmesh.py serverpack`（生成服务端，会自动从 333 个 mod 里剔掉客户端专用的，约 60~70 个）
2. 之后：双击 **`开始游戏.bat`** → 它 `pull` 存档、开服
3. 关服时**在窗口里输入 `stop` 回车**（别直接点 X），关完会自动 `push`
4. 开服窗口第一行会打印 `本机局域网地址：192.168.x.x:25565`，发给朋友

**其他人：** PCL 启动整合包 → 多人游戏 → 直接连接 → 填上面那个地址。

### 不联网也能交接：U 盘 / 任意文件夹

不想用 GitHub、或者朋友那台机器上不了网，就用文件夹搬运（菜单 `A` / `B`）：

```bat
python mcmesh.py push --to E:\           :: 导出到 U 盘（会自动建 E:\MCMesh存档\）
python mcmesh.py push --to                 :: 不给路径：列出盘符让你挑
python mcmesh.py pull --from E:\MCMesh存档 :: 从 U 盘导入
```

导出的东西长这样（一个 `.zip` 装完整存档，旁边一份说明）：

```
E:\MCMesh存档\
  mc-BetterMC-deviceflow-20261021-192554.zip     ← 这一局的完整存档（含你那份角色数据）
  mc-BetterMC-deviceflow-20261021-192554.json    ← 谁、什么时候、第几天、多大
  latest.zip                                     ← 最新那份的副本，对方一键导入用它
  latest.json
```

- 导入时**只认含 `level.dat` 的 zip**，目录里没货会直接报错，不会乱覆盖。
- 导入前会先 `backup_world` 备份你当前存档到 `backups\`。
- 角色数据（`chars\`）也在 zip 里，换房主那套移植逻辑照样有效。
- 每次最多保留 10 个 `mc-*.zip`（只删自己产生的，不动你 U 盘里别的文件）。
- 导出时如果检测到存档 45 秒内还在被写，会提醒你先退出游戏。

### 常用命令

日常其实不用命令，双击桌面的 `开始游戏.bat` / `拉取存档.bat` / `上传存档.bat` 就够了。
下面这些是它们背后等价的东西（也支持 `--force` 之类的额外参数）：

```bat
python mcmesh.py status              :: 谁什么时候上传的、本地和远端差多少
python mcmesh.py pull                :: 手动拉最新存档（会先备份本地）
python mcmesh.py push                :: 手动上传
python mcmesh.py push --force        :: 以我这一版为准，覆盖远端
python mcmesh.py pull --force        :: 无视本地改动，强制覆盖
python mcmesh.py players             :: 看成员名单和 UUID
python mcmesh.py players --add 小明 --op   :: 加人（--op 给管理员）
python mcmesh.py backup              :: 手动备份当前存档
python mcmesh.py chars               :: 看每个人的角色记录（坐标/等级/背包件数）
python mcmesh.py chars --as 名字     :: 把当前存档里的房主角色记成这个名字
python mcmesh.py chars --use 名字    :: 把某个角色套回当前存档（换房主前用）
python mcmesh.py doctor              :: 体检（找不到路径、单文件超 100MB 等都会提醒）
python mcmesh.py serverpack --dry-run      :: 只看服务端会保留/剔除哪些 mod
```

---

## 5. 身份是怎么认的（重点）

服务端在 `online-mode=false` 下（你们有人用离线号，只能这样），**"我是谁"完全等于"我的用户名"**：

```
UUID = MD5("OfflinePlayer:" + 用户名)   // 和 Java 服务端算法一致
```

所以：

- **用户名一旦定下来就不要改**。改名 = 换了一个人 = 背包清空、白名单失效。
- 每个人的进度存在存档里的 `playerdata/<UUID>.dat`，与用户名一一对应，换电脑、换主机都不丢。
- 仓库里的 `players.json` 是成员名册：

```json
{
  "players": {
    "小明": { "uuid": "…", "role": "op",   "note": "开服的人" },
    "小红": { "uuid": "…", "role": "member" }
  }
}
```

模式 B 下每次开服都会据此自动生成 `whitelist.json`（谁能进）和 `ops.json`（谁是管理员）——
所以**"记录每个人的账号信息，以自己的身份进入世界"这件事，是服务端 + 这份名册共同保证的**。

想加人：

```bat
python mcmesh.py players --add 小红
```

然后把改动 `push` 上去，别人 `pull` 后开服也认得她。

> 顺带说一句：离线模式下知道用户名的人可以冒充你。你们是熟人局域网，白名单够了；
> 真要防冒充得上 AuthMe 之类的密码插件，那是另一套东西。

### 5.1 数据到底存在哪个文件里（1.21.1）

```
世界文件夹/
  level.dat                 ← 全世界共用一份：时间、天气、种子…… **以及房主的角色**
  playerdata/<UUID>.dat     ← 每个"访客"一份：背包、坐标、等级、生命、末影箱
  stats/<UUID>.json         ← 每人一份：统计（走了多少格、挖了多少方块）
  advancements/<UUID>.json  ← 每人一份：成就
  region/ entities/ poi/    ← 地图本身（与是谁无关）
```

- **访客**的数据按 UUID 分文件存，所以谁的进度都不会丢，换主机也不丢。
- **房主**（"本地玩家"）不一样：他不在 `playerdata/` 里，他被整个写在 `level.dat` 的 `Data.Player` 里
  （这一点我查了 1.21.1 客户端 jar：`PrimaryLevelData` 的字符串常量里有 `Player`，没有 `singleplayer_uuid`）。
  于是"谁打开这个世界，谁就变成 level.dat 里存的那个人"——这就是换房主会继承背包的原因。

> 这个设计在新版存档格式里已经改了：新格式（你机器上 26.1.2 的那个存档就是）
> 变成 `players/data/<UUID>.dat` **每人一份**（连房主也有自己的一份）+ `level.dat` 里记一个
> `singleplayer_uuid` 指认世界主人。那种格式下换房主本来就不会串。
> 所以这条限制只针对 1.21.1 这类老格式。

### 5.2 角色移植：换房主也能带上自己的背包

工具会自动做两件事：

- **每次 `push`**：把当前 `level.dat` 里的房主角色抽出来，存成仓库里的 `chars/<你的名字>.dat`；
  同时把 `playerdata/` 里各访客的角色也收进 `chars/`（这样他们下次当房主也有自己的角色可用）。
- **每次开局（模式 A）**：如果 `chars/<你的名字>.dat` 存在，就先把它写回 `level.dat` 的 `Data.Player`，
  所以你永远是自己的那个角色，不会拿到别人的背包。

用的是"字节级定点替换"——只改 `Data.Player` 这一段，其它字段（时间、种子、天气、难度）一个字节都不动。
每次写入前自动把 `level.dat` 备份到 `backups/level.dat-*.bak`，写完立刻读回来比对，不一致就自动回滚。

手动兜底的两个命令：

```bat
python mcmesh.py chars               :: 看看现在 level.dat 里是谁、记录里都有谁
python mcmesh.py chars --as 小明      :: 把当前房主角色记成小明
python mcmesh.py chars --use 小明     :: 换房主前，把小明自己的角色套回去
```

### 5.3 你会同时有"两套自己"，这一点必须知道

```
chars\<你>.dat                     ← 你「当房主」时的身体（开局时会被写进 level.dat）
playerdata\<你的UUID>.dat          ← 你「当访客」时的身体（进别人世界时用的是这份）
```

这两份是**各走各的**，工具不会自动合并：

- 你当房主玩了 → 进步记在 `chars\<你>.dat`
- 你当访客玩了 → 进步记在 `playerdata\<你的UUID>.dat`

所以如果你刚去别人世界玩了两小时，回来自己开服，默认套上的是**房主版**（那两小时不在里面）。
`python mcmesh.py chars` 会把两份都列出来并提示差异；要挑一份为准：

```bat
python mcmesh.py chars --use 名字        :: 用我的「房主版」写进当前存档（默认行为）
python mcmesh.py chars --adopt-guest    :: 反过来，用我的「访客版」覆盖房主版记录
```

> 恢复某个人的角色：`chars --use 名字`。
> 从 Git 历史里捞更早的整份存档：`git -C repo log --oneline -- world/level.dat` 找到提交号，
> 再 `git -C repo show <提交号>:world/level.dat`。
> 从本机备份捞：`backups\level.dat-<时间>.bak`（每次替换前都存，保留最近 10 份）。

### 5.4 从别的存档搬玩家数据（只搬"人"，不搬地图）

旧服 / 旧存档带不进来，但里面**玩家**的背包、等级、坐标、成就、末影箱可以搬：

```bat
:: 先在新整合包里建好世界（进去站一下再退出），然后：
python mcmesh.py import-players --from "C:\Users\lanld\Desktop\mc服务器\world"
```

菜单里是 `C) 导入旧存档的玩家数据`。它会：

- 复制 `playerdata\<UUID>.dat` + `stats\<UUID>.json` + `advancements\<UUID>.json`
- 从旧世界旁边的 `usercache.json` 认回名字，并登记进 `players.json`
- 来源版本比目标新时（比如 1.21.8 → 1.21.1），把角色数据里的 `DataVersion` 改成目标版本
- 目标世界已有这个玩家就跳过；`--force` 才覆盖

**为什么能对上人**：局域网/离线服都用 `UUID = MD5("OfflinePlayer:" + 名字)`。
你旧服 `mc服务器` 是 `online-mode=false`，所以 `cood` 的 UUID 就是 `44bd5f36-383d-3978-a4b2-3eb5c5f159d5`，
和新世界里同一个名字算出来的一模一样——数据自然落回同一个人身上。
**所以名字必须完全一致**；如果某人当年是用正版账号登的（UUID 是随机那种），就搬不过去。

**搬不了的东西**：地形、建筑、已完成的地图（那些在 `region\` 里，属于"世界"，不属于"人"）。
想留旧地图只能让整个世界的版本能对上——旧版本世界能被新版本打开，反过来不行。

> 注意：`chars/*.dat` 是解压后的角色数据（方便检查），`playerdata/*.dat` 是 gzip 的，
> 工具内部会自己转换，你别手动复制粘贴这两个目录。

---

## 6. 撞车了怎么办

场景：小明玩完没上传，小红玩了并上传了，然后小明又点上传。

```
[! ] 远端有别人更新的存档，你这一版还没传上去
```

两个选择，工具不会替你自动决定：

- **以我这一版为准** → `python mcmesh.py push --force`（远端小红的进度会被覆盖，但她的本地存档还在她电脑上）
- **我要小红那版** → `python mcmesh.py pull`（会先把你本地存档打包进 `backups\`，再覆盖）

**根本解决办法是规矩**：同一时间只让一个人玩。玩完就上传，下一个人玩之前先 `pull`。
（`status` 会告诉你"最近一次是谁、什么时候上传的"，开会前看一眼就知道会不会撞。）

回滚到更早的存档（Git 自带）：

```bat
cd repo
git log --oneline
git checkout <提交号> -- world
git commit -m "回滚到 xxx"
git push
```

---

## 7. 常见问题

**Q：存档会不会很大？**
第一次几 MB 到几十 MB，之后每次只追加变化。GitHub 单文件上限 100 MB，`doctor` 会提前提醒。

**Q：朋友没装 Python/Git 怎么办？**
Git 必须装（https://git-scm.com/download/win）。Python 如果嫌麻烦，可以把你这份 `MCMesh` 文件夹连 Python 一起拷给他们——但更省事的是让他们也装个 Python 3。

**Q：能不能两个人同时玩？**
不行。这套方案的前提是"同一时间只有一个存档副本在被改"。

**Q：异地能玩吗？**
模式 A/B 默认都是局域网。异地要么用内网穿透（SakuraFrp / frp / ZeroTier），要么把端口映射出去——那属于网络配置，不在这个工具范围内。

**Q：整合包更新了怎么办？**
所有人一起更新到同一个版本再玩；服务端跑 `python mcmesh.py serverpack` 重新生成一次。

**Q：模式 A 和 B 能中途换吗？**
能。存档文件夹通用，改 `config.json` 里 `"mode"` 再跑一次 `init`（或直接改配置）即可。

---

## 8. 已知限制（我没能在这台机器上验证的部分）

- **专用服真正开起来没跑过**：`serverpack` 的 mod 剔除逻辑我拿你真实的 333 个 mod 跑过（保留 264 / 剔除 69，sodium、iris、modmenu、xaero 这些都正确排除了），Fabric 服务端 jar 的下载链路也验证过（181 KB，合法 jar）。但"开服 → 朋友连进来"这一段需要你在游戏里实际跑一次才算数。第一次开服如果崩溃，看 `repo\server\logs\latest.log`，多半是某个被标成 `environment: "*"` 的客户端 mod 需要手动加进 `server_mods_exclude`。
- **模式 A 的房主角色继承**已经用第 5.2 节的"角色移植"处理，但它是**对 `level.dat` 做手术**：
  工具会备份 + 校验 + 失败自动回滚，可万一你在这期间强杀进程，最坏情况是回滚到备份（丢一次进度）。
  稳妥做法：开局前套角色、玩完 `push`，中间不动 `config.json`。
- **首次 `push` 需要登录 GitHub**，会弹浏览器；每个人的机器各自登一次。

---

## 9. 一句话总结

```
开局前：pull     玩完：push     只有一个人在玩      名字不能改
```
