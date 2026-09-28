指挥官图片资源目录说明

1. 当前实际图片放在 Commander_Figures/，网页会直接从这里读取原图。
2. 缩略图和清晰图只是界面展示尺寸不同，不要求拆成两套文件；同一张图会按不同 UI 区块自动缩放。
3. thumbs/ 和 full/ 仍然保留为可选目录，如果以后你想手动放两套图，也可以继续用。
4. manifest.json 用于把指挥官名称绑定到图片相对路径。

manifest 推荐写法：
{
  "泽拉图": "Commander_Figures/Ui_loading_coop_hero_zeratul.png"
}

如果以后确实想区分两套图，也可以这样写：
{
  "泽拉图": {
    "thumbnail": "thumbs/zeratul.webp",
    "full": "full/zeratul.webp"
  }
}

说明：
- 路径相对于 static/commander_assets/。
- manifest 写成字符串时，thumbnail 和 full 会共用同一张图。
- 未配置图片的指挥官会自动回退成纯文字标签。
