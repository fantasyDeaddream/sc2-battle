对于其他 LLM，我建议不要只说“给头像加一个 SVG”，而是明确规定渲染层级、坐标系统、裁剪方式、SVG 数据结构和前端 API。下面这套描述基本可以直接交给其他 LLM。

推荐的整体渲染策略

核心结构：

AvatarContainer
├── AvatarImage       ← 用户上传的头像
└── AvatarFrame       ← SVG 头像框

最终效果：

┌──────────────────────┐
│        SVG头像框      │
│    ┌────────────┐    │
│    │            │    │
│    │  用户头像   │    │
│    │            │    │
│    └────────────┘    │
│        SVG头像框      │
└──────────────────────┘

其中：

用户头像始终是 <img>
头像框始终是覆盖在 <img> 上面的 <svg>
两者使用同一个正方形坐标系
SVG 中间区域必须透明，否则会遮挡头像
SVG 不负责裁剪头像，头像裁剪由 CSS 或父容器完成
头像框本身只负责绘制装饰
给其他 LLM 的完整实现说明

你可以直接把下面这段作为 Prompt：

我有一个网页项目，用户可以上传头像。

现在需要增加“头像框（Avatar Frame）”功能。

请采用“HTML Image + SVG Overlay”的方式实现，不要把头像本身嵌入 SVG。

一、总体结构

头像组件必须采用三层结构：

<div class="avatar">
    <img class="avatar-image" src="用户头像URL">
    <svg class="avatar-frame" viewBox="0 0 100 100">
        <!-- SVG头像框 -->
    </svg>
</div>

其中：

1. avatar 是正方形容器。
2. avatar-image 是用户上传的头像。
3. avatar-frame 是覆盖在头像上方的 SVG。
4. SVG 必须 pointer-events: none，不能阻挡头像点击。
5. SVG 必须 position: absolute。
6. avatar-image 和 avatar-frame 必须完全重合。

推荐：

.avatar {
    position: relative;
    width: 100px;
    aspect-ratio: 1 / 1;
}

.avatar-image {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.avatar-frame {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
}
    

二、SVG坐标系统

所有头像框统一使用：

viewBox="0 0 100 100"

不要根据实际头像尺寸修改 SVG 内部坐标。

例如：

<svg
    viewBox="0 0 100 100"
    preserveAspectRatio="xMidYMid meet"
>

以后无论头像显示为：

64×64
96×96
128×128
256×256
512×512

都直接缩放整个 SVG。

三、头像区域与头像框区域

头像框必须分为两个概念：

1. Avatar Safe Area
2. Frame Decoration Area

例如：

SVG：

<svg viewBox="0 0 100 100">

    <!-- 头像框主体 -->
    <path ... />

    <!-- 装饰 -->
    <path ... />

</svg>

其中：

中心大约 70~85% 的区域应该保持透明，让底层头像正常显示。

头像框主要绘制在：

x = 0~100
y = 0~100

的边缘区域。

不要使用一个不透明的矩形覆盖整个 SVG。

禁止：

<rect width="100" height="100" fill="white"/>

除非该矩形本身设置了透明度或者只是作为局部装饰。

四、头像裁剪

头像默认采用圆形裁剪：

.avatar-image {
    border-radius: 50%;
}

但头像框本身不要依赖 border-radius。

如果头像框设计需要特殊形状，例如：

六边形
盾牌
菱形
机械圆环
异形徽章

应该：

1. 用 SVG 本身绘制边框。
2. 必要时使用 SVG clipPath / mask。
3. 不要通过修改 img 的 border-radius 来模拟复杂头像框。

五、SVG头像框设计原则

头像框 SVG 应尽量使用：

<path>
<circle>
<rect>
<polygon>
<ellipse>
<g>

构建。

避免直接使用 raster image 作为头像框。

不要把头像框设计成一张 PNG。

SVG 必须支持：

透明背景
缩放
响应式
高 DPI
CSS 控制
颜色主题修改

六、推荐SVG结构

推荐：

<svg viewBox="0 0 100 100">

    <defs>
        <!-- 渐变 -->
        <!-- filter -->
        <!-- clipPath -->
        <!-- mask -->
    </defs>

    <g id="frame">

        <!-- 外框 -->
        <circle ... />

        <!-- 四角装饰 -->
        <path ... />

        <!-- 上方装饰 -->
        <path ... />

        <!-- 下方装饰 -->
        <path ... />

    </g>

</svg>

不同装饰应该放在不同 <g> 中。

例如：

<g id="outer-frame">
</g>

<g id="corner-decoration">
</g>

<g id="inner-frame">
</g>

<g id="special-effect">
</g>

这样以后可以动态修改。

七、头像框数据结构

前端不要直接把 SVG 字符串写死在 Avatar 组件中。

应该建立 AvatarFrame 数据结构：

{
    id: "frame_001",
    name: "星际",
    svg: "...",
    preview: "...",
    rarity: "epic"
}

或者：

{
    id: "frame_001",
    name: "星际",
    svgUrl: "/frames/frame_001.svg"
}

头像组件只负责：

1. 加载头像
2. 加载头像框
3. 将 SVG 覆盖在头像上面

例如：

<Avatar
    src={user.avatar}
    frame={user.avatarFrame}
/>

八、推荐使用外部SVG文件

头像框较多时，不建议把 SVG 全部直接写进 JavaScript。

推荐：

/assets/avatar-frames/
    ├── default.svg
    ├── bronze.svg
    ├── silver.svg
    ├── gold.svg
    ├── diamond.svg
    └── legendary.svg

前端：

<img src="/avatar.jpg">

<svg class="avatar-frame">
    <use href="/assets/avatar-frames/gold.svg#frame"></use>
</svg>

如果浏览器兼容性或跨域问题导致 <use> 引用外部 SVG 不稳定，则可以直接：

<img class="avatar-frame" src="/assets/avatar-frames/gold.svg">

但如果使用 img 加载 SVG，应确保 SVG 本身就是透明背景。

九、推荐采用“SVG透明覆盖层”而不是“SVG包住头像”

优先：

<div class="avatar">
    <img src="avatar.png">
    <img src="gold-frame.svg">
</div>

而不是：

<svg>
    <image href="avatar.png">
    ...
</svg>

原因：

1. 头像 URL 更容易管理。
2. 浏览器缓存更加简单。
3. 用户头像与头像框解耦。
4. 头像框更换不需要重新生成头像。
5. 可以给头像框单独做动画。
6. 可以单独控制头像和头像框的 CSS。
7. 后端数据库只需要保存 frame_id。

十、数据库设计

用户表不应该保存完整 SVG。

推荐只保存：

avatar_url
avatar_frame_id

例如：

{
    "avatar_url": "/uploads/user123.jpg",
    "avatar_frame_id": "gold_001"
}

服务器根据 frame_id 或前端 frame registry 获取对应 SVG。

十一、头像框尺寸与缩放

头像框必须与头像使用相同的容器尺寸。

不要出现：

头像：
100 × 100

头像框：
120 × 120

然后再通过 margin 调整位置。

正确方案：

.avatar {
    width: 100px;
    height: 100px;
}

.avatar-image {
    inset: 0;
}

.avatar-frame {
    inset: 0;
}

SVG：

viewBox="0 0 100 100"

这样 SVG 永远跟头像保持 1:1 对齐。

十二、处理头像框超出头像范围的情况

如果设计要求头像框的一部分超出头像容器：

不要直接设置：

overflow: hidden;

否则头像框可能被裁掉。

应该：

.avatar {
    position: relative;
}

.avatar-image {
    position: absolute;
    inset: 0;
    border-radius: 50%;
    overflow: hidden;
}

.avatar-frame {
    position: absolute;
    inset: -5%;
    width: 110%;
    height: 110%;
    pointer-events: none;
}

即：

头像本体负责裁剪。

头像框可以稍微扩大。

十三、需要支持动画头像框

如果某些头像框需要动态效果，例如：

发光
旋转
粒子
闪烁
流光

应该直接使用 SVG animation / CSS animation。

例如：

<svg>
    <g class="frame-glow">
        ...
    </g>
</svg>

然后：

.frame-glow {
    animation: frameGlow 2s infinite;
}

不要把动画写入用户头像图片。

十四、需要保证层级关系

推荐：

.avatar {
    position: relative;
}

.avatar-image {
    z-index: 1;
}

.avatar-frame {
    z-index: 10;
}

.avatar-badge {
    z-index: 20;
}

如果未来还需要：

VIP标识
在线状态
等级徽章
赛事排名

可以继续增加独立 overlay layer：

头像
↓
头像框
↓
状态
↓
徽章
↓
文字

十五、最终推荐结构

最终 Avatar 组件采用：

<div class="avatar">

    <!-- Layer 1 -->
    <img
        class="avatar-image"
        src="avatar.jpg"
        alt="avatar"
    />

    <!-- Layer 2 -->
    <svg
        class="avatar-frame"
        viewBox="0 0 100 100"
        preserveAspectRatio="xMidYMid meet"
    >
        ...
    </svg>

    <!-- Layer 3，可选 -->
    <div class="avatar-status"></div>

</div>

核心原则：

“头像”和“头像框”完全解耦。

头像是用户数据。

头像框是装饰数据。

最终渲染只是将两者叠加。

请按照这个架构设计组件，并给出：

1. HTML/CSS 示例
2. 一个完整的 SVG 头像框示例
3. AvatarFrame 数据结构
4. 头像框切换代码
5. 多种尺寸下保持比例的实现
6. 如果项目使用 React/Vue，请分别给出组件实现
7. 说明如何避免 SVG 遮挡头像
8. 说明如何实现头像框动画
我更推荐的实际架构

如果你的项目后续准备做成类似游戏/赛事网站的头像系统，建议进一步采用：

                Avatar Component
                       │
             ┌─────────┴─────────┐
             ↓                   ↓
        Avatar Image        Avatar Frame
             │                   │
        avatar_url          frame_id
                                  │
                           Frame Registry
                                  │
                ┌─────────────────┼─────────────────┐
                ↓                 ↓                 ↓
            bronze.svg         gold.svg        legendary.svg

数据库只存：

{
  "avatar_url": "/uploads/avatar/123.jpg",
  "frame_id": "legendary_001"
}

而不要存：

{
  "avatar": "一大段Base64...",
  "svg": "一大段SVG..."
}

这样以后你可以很容易实现：

默认头像框
青铜头像框
白银头像框
黄金头像框
钻石头像框
赛事冠军头像框
活动限定头像框
动态头像框

甚至可以进一步做成：

frame_id
frame_type
frame_rarity
frame_svg
frame_animation
frame_unlock_condition
frame_expire_at

这样你的头像框实际上就已经从“一个装饰图片”升级成了一个可配置的 UI 资源系统。

一个很关键的设计细节

如果你的头像框主要用于圆形头像，我建议统一规定一个“安全区域”，例如：

SVG 100×100

┌─────────────────────┐
│ ███████████████████ │  ← 头像框
│ ██               ██ │
│ ██   SAFE AREA    ██ │
│ ██     70~80%     ██ │
│ ██               ██ │
│ ███████████████████ │
└─────────────────────┘

也就是说，其他 LLM 在生成 SVG 时，应明确要求：

中心区域保持透明，头像框装饰主要集中在外圈 10%~20% 区域，不得使用实体背景填充覆盖中心头像区域。

这一条非常重要。否则 LLM 很容易给你生成一个“完整 SVG 卡片”，最后把用户头像整个盖住。