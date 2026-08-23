import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "C:/Users/Mechrevo/Documents/ChatGPT/PetTrip/pilot4mvp3";
const outputDir = path.join(root, "outputs", "mask-stability-validation-20260818");
const previewDir = path.join(root, ".xlsx-build", "previews");
const outputPath = path.join(outputDir, "mask-stability-validation.xlsx");

const readJson = async (relative) => JSON.parse(await fs.readFile(path.join(root, relative), "utf8"));
const manifest = await readJson("experiment-manifest.json");
const groupsPayload = await readJson("references/task-groups.json");
const catalog = await readJson("references/reference-catalog.json");
const automatic = await readJson("reviews/automatic-measurements.json");
const manualPayload = await readJson("reviews/manual-review-ratings.json");
const assets = await readJson(".xlsx-build/workbook-assets.json");
const selectionMarkdown = await fs.readFile(path.join(root, "references/selection-analysis.md"), "utf8");

const absolute = (relative) => path.resolve(root, relative).replaceAll("\\", "/");
const imageDataUrl = async (relative) => {
  const thumb = path.join(root, assets[relative]);
  const bytes = await fs.readFile(thumb);
  return `data:image/jpeg;base64,${bytes.toString("base64")}`;
};
const stripHtml = (value) => String(value ?? "")
  .replace(/<br\s*\/?\s*>/gi, " ")
  .replace(/<[^>]+>/g, "")
  .replace(/\s+/g, " ")
  .trim();

const selectionRows = new Map();
for (const line of selectionMarkdown.split(/\r?\n/)) {
  if (!/^\| C\d{2} \|/.test(line)) continue;
  const cells = line.split("|").slice(1, -1).map((cell) => cell.trim());
  selectionRows.set(cells[0], {
    composition: cells[1],
    risk: cells[2],
    selection: cells[3],
  });
}

const groups = new Map(groupsPayload.selected_groups.map((group) => [group.id, group]));
const ratings = manualPayload.ratings;
const experiments = manifest.experiments;

const workbook = Workbook.create();
const summary = workbook.worksheets.add("结果总览");
const data = workbook.worksheets.add("实验数据");
const gallery = workbook.worksheets.add("实验图册");
const prompts = workbook.worksheets.add("Prompt全文");
const scenes = workbook.worksheets.add("场景与风格");
const candidates = workbook.worksheets.add("参考候选");

const colors = {
  ink: "#202825",
  moss: "#61745D",
  mossLight: "#DDE5DA",
  sky: "#DCE9EC",
  coral: "#C66B4E",
  coralLight: "#F2DED6",
  paper: "#F7F8F5",
  white: "#FFFFFF",
  muted: "#66706B",
  line: "#C9D0CB",
  fail: "#F3D4CF",
  warn: "#F1E6B8",
  pass: "#D7E7D3",
};

const titleStyle = {
  fill: colors.ink,
  font: { bold: true, color: colors.white, size: 18 },
  verticalAlignment: "center",
};
const headerStyle = {
  fill: colors.moss,
  font: { bold: true, color: colors.white },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "bottom", style: "medium", color: colors.ink },
};
const subheaderStyle = {
  fill: colors.mossLight,
  font: { bold: true, color: colors.ink },
  verticalAlignment: "center",
  wrapText: true,
};
const bodyStyle = {
  fill: colors.paper,
  font: { color: colors.ink, size: 10 },
  verticalAlignment: "top",
  wrapText: true,
};
const thinBottom = { preset: "bottom", style: "thin", color: colors.line };

for (const sheet of [summary, data, gallery, prompts, scenes, candidates]) {
  sheet.showGridLines = false;
}

// Results summary.
summary.mergeCells("A1:J2");
summary.getRange("A1").values = [["Mask 稳定性验证 · 结果总览"]];
summary.getRange("A1:J2").format = titleStyle;
summary.getRange("A3:J3").merge();
summary.getRange("A3").values = [["Neva 风格 · 若叶睦 Q 版 · 8 组 / 16 元素 / 32 实验单元 / 64 次图片调用"]];
summary.getRange("A3:J3").format = {
  fill: colors.sky,
  font: { color: colors.ink, italic: true },
  verticalAlignment: "center",
};

const cards = [
  ["A5:B5", "A6:B7", "实验单元", "=COUNTA('实验数据'!$A$2:$A$33)", "0"],
  ["C5:D5", "C6:D7", "Mask 直径中位数", "=MEDIAN('实验数据'!$J$2:$J$33)", "0.00\" px\""],
  ["E5:F5", "E6:F7", "108±10px 命中", "=COUNTIFS('实验数据'!$J$2:$J$33,\">=98\",'实验数据'!$J$2:$J$33,\"<=118\")", "0\" / 32\""],
  ["G5:H5", "G6:H7", "Mask 位置均分", "=AVERAGE('实验数据'!$T$2:$T$33)", "0.00\" / 5\""],
  ["I5:J5", "I6:J7", "角色服从均分", "=AVERAGE('实验数据'!$Y$2:$Y$33)", "0.00\" / 5\""],
];
for (const [labelRange, valueRange, label, formula, numberFormat] of cards) {
  summary.mergeCells(labelRange);
  summary.mergeCells(valueRange);
  summary.getRange(labelRange.split(":")[0]).values = [[label]];
  summary.getRange(valueRange.split(":")[0]).formulas = [[formula]];
  summary.getRange(labelRange).format = subheaderStyle;
  summary.getRange(valueRange).format = {
    fill: colors.white,
    font: { bold: true, color: colors.coral, size: 18 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: colors.line },
    numberFormat,
  };
}

summary.mergeCells("A9:J10");
summary.getRange("A9").values = [["结论：路线整体不通过。image-2 能理解场景语义并选择大致合理的区域，也能把角色生成在黑圆附近；但不能可靠生成固定 108px Mask，不能保证角色严格包含在 Mask 内，也不能维持圆外像素完全不变。"]];
summary.getRange("A9:J10").format = {
  fill: colors.coralLight,
  font: { bold: true, color: colors.ink, size: 12 },
  wrapText: true,
  verticalAlignment: "center",
  borders: { preset: "outside", style: "medium", color: colors.coral },
};

summary.getRange("A12:C12").values = [["人工维度", "均分", "判断"]];
summary.getRange("A12:C12").format = headerStyle;
const dimensionRows = [
  ["Mask 语义位置", "=AVERAGE('实验数据'!$T$2:$T$33)", "多数能找到正确地标附近区域"],
  ["Mask 固定尺寸", "=AVERAGE('实验数据'!$U$2:$U$33)", "明显失败"],
  ["Mask 可站立性", "=AVERAGE('实验数据'!$V$2:$V$33)", "大多数合理，海岸和远岸有错误"],
  ["单圆清晰度", "=AVERAGE('实验数据'!$W$2:$W$33)", "32/32 清晰单圆"],
  ["角色身份可辨", "=AVERAGE('实验数据'!$X$2:$X$33)", "中大尺寸可辨，小圆不可读"],
  ["角色严格服从 Mask", "=AVERAGE('实验数据'!$Y$2:$Y$33)", "32/32 有不同程度越界"],
  ["角色接地", "=AVERAGE('实验数据'!$Z$2:$Z$33)", "多数脚底关系自然"],
  ["指定互动动作", "=AVERAGE('实验数据'!$AA$2:$AA$33)", "多数退化为普通站立"],
  ["Neva 风格与场景保持", "=AVERAGE('实验数据'!$AB$2:$AB$33)", "场景统一，角色仍有贴入感"],
];
summary.getRange(`A13:C${12 + dimensionRows.length}`).values = dimensionRows.map(([label, , note]) => [label, null, note]);
summary.getRange(`B13:B${12 + dimensionRows.length}`).formulas = dimensionRows.map(([, formula]) => [formula]);
summary.getRange(`A13:C${12 + dimensionRows.length}`).format = { ...bodyStyle, borders: thinBottom };
summary.getRange(`B13:B${12 + dimensionRows.length}`).format.numberFormat = "0.00";

summary.getRange("E12:J12").values = [["推荐下一路线", null, null, null, null, null]];
summary.mergeCells("E12:J12");
summary.getRange("E12:J12").format = headerStyle;
summary.mergeCells("E13:J17");
summary.getRange("E13").values = [["视觉模型输出语义目标 + 3 个候选中心点 / 允许区域\n→ 规则检查可站立性与遮挡\n→ 确定性程序绘制固定 108px Mask\n→ image-2 根据程序 Mask 生成角色\n→ 人工或视觉模型复核角色与背景"]];
summary.getRange("E13:J17").format = {
  ...bodyStyle,
  fill: colors.sky,
  font: { bold: true, color: colors.ink, size: 12 },
  verticalAlignment: "center",
  borders: { preset: "outside", style: "thin", color: colors.line },
};
summary.mergeCells("E19:J21");
summary.getRange("E19").values = [["边界：本轮没有验证专用 VLM 直接输出坐标、程序 Mask 后的角色稳定性、角色 bbox 自动检测、Unity 坐标转换与点击热区。"]];
summary.getRange("E19:J21").format = { ...bodyStyle, fill: colors.white, borders: { preset: "outside", style: "thin", color: colors.line } };

summary.getRange("A24:J24").values = [["工作簿导航", null, null, null, null, null, null, null, null, null]];
summary.mergeCells("A24:J24");
summary.getRange("A24:J24").format = headerStyle;
summary.getRange("A25:J30").values = [
  ["实验数据", "32 行原始自动测量、人工评分与公式结果", null, null, null, null, null, null, null, null],
  ["实验图册", "32 个单元的 Mask / 角色图片并排审阅", null, null, null, null, null, null, null, null],
  ["Prompt全文", "8 个场景 Prompt + 32 个 Mask Prompt + 32 个角色 Prompt", null, null, null, null, null, null, null, null],
  ["场景与风格", "8 张最终 Neva 场景、角色参考与 2 张官方风格参考", null, null, null, null, null, null, null, null],
  ["参考候选", "20 张 Wikimedia 候选、视觉分析、许可和来源 URL", null, null, null, null, null, null, null, null],
  ["评分说明", "1=失败，3=部分可用，5=满足冻结要求；人工评分是本轮 Codex 首轮视觉复核", null, null, null, null, null, null, null, null],
];
for (let row = 25; row <= 30; row += 1) summary.mergeCells(`B${row}:J${row}`);
summary.getRange("A25:J30").format = { ...bodyStyle, borders: thinBottom };
summary.getRange("A25:A30").format.font = { bold: true, color: colors.moss };
summary.getRange("A1:J30").format.rowHeight = 24;
summary.getRange("A1:J2").format.rowHeight = 34;
summary.getRange("A9:J10").format.rowHeight = 42;
summary.getRange("A1:J30").format.columnWidthPx = 105;
summary.getRange("A1:A30").format.columnWidthPx = 145;
summary.getRange("B1:B30").format.columnWidthPx = 115;
summary.freezePanes.freezeRows(3);

// Flat experimental data and formulas.
const headers = [
  "实验 ID", "组", "元素", "轮次", "场景", "目标", "风险", "动作", "目标直径 px",
  "实测直径 px", "直径误差 px", "圆心 X", "圆心 Y", "两轮圆心漂移 px", "两轮直径差 px",
  "Mask 圆外 MAE", "Mask 圆外 >30", "角色圆外 MAE", "角色圆外 >30",
  "Mask 位置", "Mask 尺寸", "Mask 地面", "单圆清晰", "角色身份", "角色服从", "角色接地",
  "互动", "风格保持", "Mask 均分", "角色均分", "单元通过", "失败标签", "人工备注",
  "Mask 原图路径", "角色原图路径", "场景 Prompt 路径", "Mask Prompt 路径", "角色 Prompt 路径",
];
data.getRange(`A1:AL1`).values = [headers];
data.getRange("A1:AL1").format = headerStyle;
const dataRows = experiments.map((entry) => {
  const auto = automatic.experiments[entry.id];
  const mask = auto.mask;
  const pair = automatic.pair_stability[`${entry.group_id}-${entry.element_id}`];
  const score = ratings[entry.id];
  const group = groups.get(entry.group_id);
  return [
    entry.id, entry.group_id, entry.element_id, entry.round, group.scene_name, entry.target, entry.risk, entry.action,
    108, mask.equivalent_diameter_px, mask.diameter_error_px, mask.center_px[0], mask.center_px[1],
    pair.round_center_distance_px, pair.round_diameter_absolute_difference_px,
    auto.mask_outside_difference.mean_absolute_channel_difference,
    auto.mask_outside_difference.pixels_changed_over_30_ratio,
    auto.character_outside_former_mask_difference.mean_absolute_channel_difference,
    auto.character_outside_former_mask_difference.pixels_changed_over_30_ratio,
    score.mask_position_score_1_5, score.mask_size_score_1_5, score.mask_ground_score_1_5, score.mask_single_clear_score_1_5,
    score.character_present_identity_score_1_5, score.character_placement_score_1_5,
    score.character_grounding_score_1_5, score.interaction_score_1_5, score.scene_style_preservation_score_1_5,
    null, null, null, score.failure_tags, score.review_note,
    absolute(entry.mask_output), absolute(entry.character_output), absolute(`prompts/${entry.group_id}-scene.txt`),
    absolute(entry.mask_prompt), absolute(entry.character_prompt),
  ];
});
data.getRange(`A2:AL${experiments.length + 1}`).values = dataRows;
for (let row = 2; row <= experiments.length + 1; row += 1) {
  data.getRange(`AC${row}`).formulas = [[`=AVERAGE(T${row}:W${row})`]];
  data.getRange(`AD${row}`).formulas = [[`=AVERAGE(X${row}:AB${row})`]];
  data.getRange(`AE${row}`).formulas = [[`=IF(AND(AC${row}>=4,AD${row}>=4),\"PASS\",\"FAIL\")`]];
}
data.getRange(`A2:AL${experiments.length + 1}`).format = { ...bodyStyle, borders: thinBottom };
data.getRange(`I2:S${experiments.length + 1}`).format.numberFormat = "0.00";
data.getRange(`Q2:Q${experiments.length + 1}`).format.numberFormat = "0.0%";
data.getRange(`S2:S${experiments.length + 1}`).format.numberFormat = "0.0%";
data.getRange(`T2:AB${experiments.length + 1}`).format.numberFormat = "0";
data.getRange(`AC2:AD${experiments.length + 1}`).format.numberFormat = "0.00";
data.getRange(`AE2:AE${experiments.length + 1}`).format.font = { bold: true, color: colors.coral };
data.getRange("A:AL").format.columnWidthPx = 95;
data.getRange("A:A").format.columnWidthPx = 105;
data.getRange("E:H").format.columnWidthPx = 170;
data.getRange("AF:AG").format.columnWidthPx = 260;
data.getRange("AH:AL").format.columnWidthPx = 280;
data.getRange(`A2:AL${experiments.length + 1}`).format.rowHeightPx = 58;
data.freezePanes.freezeRows(1);
data.freezePanes.freezeColumns(1);
const dataTable = data.tables.add(`A1:AL${experiments.length + 1}`, true, "ExperimentDataTable");
dataTable.style = "TableStyleMedium4";

// Experiment image gallery.
gallery.getRange("A1:I1").values = [["实验 ID", "目标", "Mask 输出", "角色输出", "直径 px", "Mask 均分", "角色均分", "失败标签", "人工备注"]];
gallery.getRange("A1:I1").format = headerStyle;
gallery.getRange(`A2:I${experiments.length + 1}`).values = experiments.map((entry, index) => {
  const auto = automatic.experiments[entry.id];
  const score = ratings[entry.id];
  return [entry.id, entry.target, null, null, auto.mask.equivalent_diameter_px, null, null, score.failure_tags, score.review_note];
});
for (let row = 2; row <= experiments.length + 1; row += 1) {
  gallery.getRange(`F${row}`).formulas = [[`='实验数据'!AC${row}`]];
  gallery.getRange(`G${row}`).formulas = [[`='实验数据'!AD${row}`]];
}
gallery.getRange(`A2:I${experiments.length + 1}`).format = { ...bodyStyle, borders: thinBottom };
gallery.getRange("A:A").format.columnWidthPx = 105;
gallery.getRange("B:B").format.columnWidthPx = 205;
gallery.getRange("C:D").format.columnWidthPx = 300;
gallery.getRange("E:G").format.columnWidthPx = 85;
gallery.getRange("H:H").format.columnWidthPx = 230;
gallery.getRange("I:I").format.columnWidthPx = 310;
gallery.getRange(`A2:I${experiments.length + 1}`).format.rowHeightPx = 164;
gallery.getRange(`E2:G${experiments.length + 1}`).format.numberFormat = "0.00";
for (let index = 0; index < experiments.length; index += 1) {
  const entry = experiments[index];
  gallery.images.add({
    dataUrl: await imageDataUrl(entry.mask_output),
    anchor: { from: { row: index + 1, col: 2 }, extent: { widthPx: 286, heightPx: 161 } },
  });
  gallery.images.add({
    dataUrl: await imageDataUrl(entry.character_output),
    anchor: { from: { row: index + 1, col: 3 }, extent: { widthPx: 286, heightPx: 161 } },
  });
}
gallery.freezePanes.freezeRows(1);
gallery.freezePanes.freezeColumns(2);

// Full prompt archive.
prompts.getRange("A1:H1").values = [["实验 ID", "目标", "场景 Prompt", "Mask Prompt", "角色 Prompt", "场景 Prompt 文件", "Mask Prompt 文件", "角色 Prompt 文件"]];
prompts.getRange("A1:H1").format = headerStyle;
const promptRows = [];
for (const entry of experiments) {
  const scenePromptPath = `prompts/${entry.group_id}-scene.txt`;
  promptRows.push([
    entry.id,
    entry.target,
    await fs.readFile(path.join(root, scenePromptPath), "utf8"),
    await fs.readFile(path.join(root, entry.mask_prompt), "utf8"),
    await fs.readFile(path.join(root, entry.character_prompt), "utf8"),
    absolute(scenePromptPath),
    absolute(entry.mask_prompt),
    absolute(entry.character_prompt),
  ]);
}
prompts.getRange(`A2:H${promptRows.length + 1}`).values = promptRows;
prompts.getRange(`A2:H${promptRows.length + 1}`).format = { ...bodyStyle, borders: thinBottom };
prompts.getRange("A:A").format.columnWidthPx = 105;
prompts.getRange("B:B").format.columnWidthPx = 210;
prompts.getRange("C:E").format.columnWidthPx = 470;
prompts.getRange("F:H").format.columnWidthPx = 270;
prompts.getRange(`A2:H${promptRows.length + 1}`).format.rowHeightPx = 255;
prompts.freezePanes.freezeRows(1);
prompts.freezePanes.freezeColumns(2);

// Final scenes, character anchor, and actual style references.
scenes.getRange("A1:G1").values = [["类型 / 组", "名称", "元素 / 用途", "Prompt 全文", "图片", "原图路径", "备注"]];
scenes.getRange("A1:G1").format = headerStyle;
const rolePrompt = await fs.readFile(path.join(root, "prompts/role-reference.txt"), "utf8");
const sceneRows = [
  ["角色锚点", "若叶睦 Q 版", "固定身份参考", rolePrompt, null, absolute("references/mutsumi-chibi-reference-neva-v2.png"), "灰蓝短发、青绿色眼睛、冷灰蓝服装"],
  ["Neva 参考", "官方截图 06", "块面、剪影、笔触、层级与光色", "仅作风格参考，不复制构图或事件", null, absolute("references/neva-official/neva-steam-06.jpg"), "Steam 官方截图"],
  ["Neva 参考", "官方截图 14", "块面、剪影、笔触、层级与光色", "仅作风格参考，不复制构图或事件", null, absolute("references/neva-official/neva-steam-14.jpg"), "Steam 官方截图"],
];
const sceneImages = [
  "references/mutsumi-chibi-reference-neva-v2.png",
  "references/neva-official/neva-steam-06.jpg",
  "references/neva-official/neva-steam-14.jpg",
];
for (const group of groupsPayload.selected_groups) {
  const scenePromptPath = `prompts/${group.id}-scene.txt`;
  const scenePath = `runs/${group.id}/scene-neva-v2.png`;
  sceneRows.push([
    group.id,
    group.scene_name,
    group.elements.map((item) => `${item.id} ${item.name}`).join("；"),
    await fs.readFile(path.join(root, scenePromptPath), "utf8"),
    null,
    absolute(scenePath),
    `${group.palette}；参考 ${group.reference_id}`,
  ]);
  sceneImages.push(scenePath);
}
scenes.getRange(`A2:G${sceneRows.length + 1}`).values = sceneRows;
scenes.getRange(`A2:G${sceneRows.length + 1}`).format = { ...bodyStyle, borders: thinBottom };
scenes.getRange("A:A").format.columnWidthPx = 105;
scenes.getRange("B:B").format.columnWidthPx = 180;
scenes.getRange("C:C").format.columnWidthPx = 300;
scenes.getRange("D:D").format.columnWidthPx = 520;
scenes.getRange("E:E").format.columnWidthPx = 330;
scenes.getRange("F:F").format.columnWidthPx = 320;
scenes.getRange("G:G").format.columnWidthPx = 190;
scenes.getRange(`A2:G${sceneRows.length + 1}`).format.rowHeightPx = 185;
for (let index = 0; index < sceneImages.length; index += 1) {
  scenes.images.add({
    dataUrl: await imageDataUrl(sceneImages[index]),
    anchor: { from: { row: index + 1, col: 4 }, extent: { widthPx: 320, heightPx: 180 } },
  });
}
scenes.freezePanes.freezeRows(1);

// All twenty source candidates and provenance.
candidates.getRange("A1:I1").values = [["ID", "选择", "图片", "标题", "构图分析", "Mask 风险", "许可", "来源 URL", "本地原图路径"]];
candidates.getRange("A1:I1").format = headerStyle;
const candidateRows = catalog.map((item) => {
  const analysis = selectionRows.get(item.id);
  return [
    item.id,
    analysis.selection,
    null,
    item.title,
    analysis.composition,
    analysis.risk,
    `${item.license} · ${stripHtml(item.author).slice(0, 180)}`,
    item.file_page_url,
    absolute(item.local_path),
  ];
});
candidates.getRange(`A2:I${candidateRows.length + 1}`).values = candidateRows;
candidates.getRange(`A2:I${candidateRows.length + 1}`).format = { ...bodyStyle, borders: thinBottom };
candidates.getRange("A:A").format.columnWidthPx = 55;
candidates.getRange("B:B").format.columnWidthPx = 110;
candidates.getRange("C:C").format.columnWidthPx = 300;
candidates.getRange("D:D").format.columnWidthPx = 280;
candidates.getRange("E:F").format.columnWidthPx = 300;
candidates.getRange("G:G").format.columnWidthPx = 220;
candidates.getRange("H:I").format.columnWidthPx = 310;
candidates.getRange(`A2:I${candidateRows.length + 1}`).format.rowHeightPx = 164;
for (let index = 0; index < catalog.length; index += 1) {
  candidates.images.add({
    dataUrl: await imageDataUrl(catalog[index].local_path),
    anchor: { from: { row: index + 1, col: 2 }, extent: { widthPx: 286, heightPx: 161 } },
  });
}
candidates.freezePanes.freezeRows(1);
candidates.freezePanes.freezeColumns(2);

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const previews = [
  ["结果总览", "A1:J30", "summary.png"],
  ["实验数据", "A1:AG12", "data-top.png"],
  ["实验图册", "A1:I7", "gallery-top.png"],
  ["实验图册", "A28:I33", "gallery-bottom.png"],
  ["Prompt全文", "A1:H4", "prompts-top.png"],
  ["Prompt全文", "A30:H33", "prompts-bottom.png"],
  ["场景与风格", `A1:G${sceneRows.length + 1}`, "scenes.png"],
  ["参考候选", "A1:I8", "candidates-top.png"],
  ["参考候选", "A16:I21", "candidates-bottom.png"],
];
for (const [sheetName, range, fileName] of previews) {
  const blob = await workbook.render({ sheetName, range, scale: 0.7, format: "png" });
  await fs.writeFile(path.join(previewDir, fileName), new Uint8Array(await blob.arrayBuffer()));
}

const inspectSummary = await workbook.inspect({
  kind: "table",
  range: "结果总览!A1:J30",
  include: "values,formulas",
  tableMaxRows: 30,
  tableMaxCols: 10,
  maxChars: 5000,
});
console.log(inspectSummary.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`saved ${outputPath}`);
