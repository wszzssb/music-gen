/* gm.js —— General MIDI 128 音色的中文名 + 16 个大类 + "大类→小类"两级选择（2026-10-08）
 *
 * 用户口径："选弦乐等大类能不能下面有小类选"。原来只有 12 个"常用乐器"按钮，
 * 想选"弦乐里的中提琴""簧管里的英国管""合成垫里的弓弦垫"就没有入口。这里给全表。
 *
 * ⚠ **名字只活在前端**：两端只传 **GM 号**（`--lead 65` / `--instrument 65`），
 *   后端不必再维护一张 128 项的表，也不会出现"前端有名字、后端不认"的漂移。
 * ⚠ 大类划分 = General MIDI 标准的 16 组（每 8 个一组），不是我拍的。
 */
window.GM_GROUPS = [
  { name: '钢琴', from: 0, to: 7 },
  { name: '半音打击', from: 8, to: 15 },
  { name: '风琴', from: 16, to: 23 },
  { name: '吉他', from: 24, to: 31 },
  { name: '贝斯', from: 32, to: 39 },
  { name: '弦乐', from: 40, to: 47 },
  { name: '合奏', from: 48, to: 55 },
  { name: '铜管', from: 56, to: 63 },
  { name: '簧管', from: 64, to: 71 },
  { name: '吹管', from: 72, to: 79 },
  { name: '合成主音', from: 80, to: 87 },
  { name: '合成垫', from: 88, to: 95 },
  { name: '合成效果', from: 96, to: 103 },
  { name: '民族', from: 104, to: 111 },
  { name: '打击乐', from: 112, to: 119 },
  { name: '音效', from: 120, to: 127 }
];

window.GM_NAMES = {
  0: '原声大钢琴', 1: '明亮钢琴', 2: '电钢琴', 3: '酒吧钢琴',
  4: '电钢琴 1', 5: '电钢琴 2', 6: '大键琴', 7: '击弦古钢琴',
  8: '钢片琴', 9: '钟琴', 10: '音乐盒', 11: '颤音琴',
  12: '马林巴', 13: '木琴', 14: '管钟', 15: '扬琴',
  16: '击杆风琴', 17: '打击风琴', 18: '摇滚风琴', 19: '教堂管风琴',
  20: '簧风琴', 21: '手风琴', 22: '口琴', 23: '探戈手风琴',
  24: '尼龙弦吉他', 25: '钢弦吉他', 26: '爵士电吉他', 27: '清音电吉他',
  28: '闷音电吉他', 29: '过载吉他', 30: '失真吉他', 31: '吉他泛音',
  32: '原声贝斯', 33: '指弹贝斯', 34: '拨片贝斯', 35: '无品贝斯',
  36: '击弦贝斯 1', 37: '击弦贝斯 2', 38: '合成贝斯 1', 39: '合成贝斯 2',
  40: '小提琴', 41: '中提琴', 42: '大提琴', 43: '低音提琴',
  44: '弦乐震音', 45: '弦乐拨奏', 46: '竖琴', 47: '定音鼓',
  48: '弦乐合奏 1', 49: '弦乐合奏 2', 50: '合成弦乐 1', 51: '合成弦乐 2',
  52: '人声「啊」', 53: '人声「哦」', 54: '合成人声', 55: '管弦乐齐奏',
  56: '小号', 57: '长号', 58: '大号', 59: '弱音小号',
  60: '圆号', 61: '铜管组', 62: '合成铜管 1', 63: '合成铜管 2',
  64: '高音萨克斯', 65: '中音萨克斯', 66: '次中音萨克斯', 67: '上低音萨克斯',
  68: '双簧管', 69: '英国管', 70: '巴松管', 71: '单簧管',
  72: '短笛', 73: '长笛', 74: '竖笛', 75: '排箫',
  76: '吹瓶声', 77: '尺八', 78: '哨子', 79: '陶笛',
  80: '方波主音', 81: '锯齿主音', 82: '汽笛风琴主音', 83: '吹管主音',
  84: '方波主音 2', 85: '沙锤主音', 86: '人声主音', 87: '五度主音',
  88: '合成垫 1 新时代', 89: '合成垫 2 温暖', 90: '合成垫 3 复音', 91: '合成垫 4 合唱',
  92: '合成垫 5 弓弦', 93: '合成垫 6 金属', 94: '合成垫 7 光环', 95: '合成垫 8 扫掠',
  96: '雨声', 97: '音轨', 98: '水晶', 99: '大气',
  100: '明亮', 101: '哥布林', 102: '回声', 103: '科幻',
  104: '西塔琴', 105: '班卓琴', 106: '三味线', 107: '古筝',
  108: '卡林巴', 109: '风笛', 110: '提琴', 111: '希尔尼',
  112: '叮当铃', 113: '阿哥哥', 114: '钢鼓', 115: '木鱼',
  116: '太鼓', 117: '旋律鼓', 118: '合成鼓', 119: '反镲',
  120: '吉他品噪', 121: '呼吸声', 122: '海浪', 123: '鸟鸣',
  124: '电话铃', 125: '直升机', 126: '掌声', 127: '枪声'
};

window.gmName = function (n) {
  return window.GM_NAMES[n] || ('GM ' + n);
};

/* **两级选择组件**：`selId` 是大类下拉、`chipsId` 是小类按钮容器。
 * `onPick(gmNumber)` 在选中某个音色时回调（传号，不传名字）。
 * `cur` = 初始选中的 GM 号（null = 一个都不选，由调用方给"不指定"按钮）。 */
window.gmBind = function (selId, chipsId, onPick, cur) {
  var sel = document.getElementById(selId);
  var box = document.getElementById(chipsId);
  if (!sel || !box) { return; }
  // 大类下拉：只有"含 cur 的那一类"要能定位到，其余从第一类开始
  sel.innerHTML = '';
  for (var i = 0; i < window.GM_GROUPS.length; i++) {
    var o = document.createElement('option');
    o.value = String(i);
    o.textContent = window.GM_GROUPS[i].name;
    sel.appendChild(o);
  }
  function render() {
    var g = window.GM_GROUPS[Number(sel.value) || 0];
    box.innerHTML = '';
    for (var g2 = g.from; g2 <= g.to; g2++) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip' + (cur === g2 ? ' on' : '');
      b.setAttribute('data-gm', String(g2));
      b.textContent = g2 + ' ' + window.gmName(g2);
      b.onclick = (function (num) {
        return function (e) {
          if (e) { e.preventDefault(); }
          cur = num;
          var all = box.querySelectorAll('button.chip');
          for (var k = 0; k < all.length; k++) { all[k].classList.remove('on'); }
          this.classList.add('on');
          onPick(num);
        };
      })(g2);
      box.appendChild(b);
    }
  }
  sel.onchange = render;
  // 初始化：若已有选中值，先切到它所在的大类
  if (cur != null) {
    for (var j = 0; j < window.GM_GROUPS.length; j++) {
      if (cur >= window.GM_GROUPS[j].from && cur <= window.GM_GROUPS[j].to) {
        sel.value = String(j);
        break;
      }
    }
  }
  render();
};
