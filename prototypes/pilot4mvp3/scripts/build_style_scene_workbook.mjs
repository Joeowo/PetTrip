import fs from "node:fs/promises";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve(import.meta.dirname, "..");
const sourcePath = path.resolve(root, "..", "docs", "画风素材库_42条.xlsx");
const experimentDir = path.join(root, "style-scene-experiment-20260819");
const manifestPath = path.join(experimentDir, "manifest.json");
const thumbnailDir = path.join(root, ".xlsx-build", "style-scene-thumbs");
const outputDir = path.join(root, "outputs", "style-scene-experiment-20260819");
const outputPath = path.join(outputDir, "画风场景生成结果_阿尔卑斯山谷小屋.xlsx");
const previewDir = path.join(root, ".xlsx-build", "style-scene-preview");

const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
if (manifest.records.length !== 42) {
  throw new Error(`expected 42 manifest records, found ${manifest.records.length}`);
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourcePath));
const sheet = workbook.worksheets.add("阿尔卑斯生成结果");
sheet.showGridLines = false;
sheet.freezePanes.freezeRows(1);

const headers = [[
  "编号",
  "画风名称",
  "游戏名字",
  "风格关键词",
  "目标场景",
  "模型参数",
  "实际生成提示词",
  "生成结果图",
  "项目内原图路径",
]];
sheet.getRange("A1:I1").values = headers;

const rows = [];
for (const record of manifest.records) {
  const prompt = await fs.readFile(path.join(root, record.prompt), "utf8");
  rows.push([
    record.id,
    record.styleName,
    record.gameName,
    record.keywords,
    record.scene,
    `${record.model} | ${record.size} | quality=${record.quality} | 3张风格参考图`,
    prompt,
    "",
    record.output,
  ]);
}
sheet.getRange("A2:I43").values = rows;

const header = sheet.getRange("A1:I1");
header.format.fill = "#1F2933";
header.format.font = { bold: true, color: "#FFFFFF", size: 11 };
header.format.horizontalAlignment = "center";
header.format.verticalAlignment = "center";
header.format.rowHeightPx = 34;
header.format.borders = { preset: "all", style: "thin", color: "#3F4B57" };

const body = sheet.getRange("A2:I43");
body.format.font = { color: "#1F2933", size: 10 };
body.format.verticalAlignment = "top";
body.format.wrapText = true;
body.format.borders = { preset: "all", style: "thin", color: "#D8DEE4" };
body.format.rowHeightPx = 218;

sheet.getRange("A2:A43").format.horizontalAlignment = "center";
sheet.getRange("A2:A43").format.font = { bold: true, color: "#2F6F61", size: 10 };
sheet.getRange("B2:B43").format.font = { bold: true, color: "#1F2933", size: 10 };
sheet.getRange("F2:F43").format.fill = "#EEF5F2";
sheet.getRange("I2:I43").format.fill = "#F4F6F8";

const widths = [62, 130, 150, 250, 125, 210, 430, 480, 310];
for (let column = 0; column < widths.length; column += 1) {
  sheet.getRangeByIndexes(0, column, 43, 1).format.columnWidthPx = widths[column];
}

for (let index = 0; index < manifest.records.length; index += 1) {
  const record = manifest.records[index];
  const bytes = await fs.readFile(path.join(thumbnailDir, `${record.id}.jpg`));
  const dataUrl = `data:image/jpeg;base64,${bytes.toString("base64")}`;
  sheet.images.add({
    dataUrl,
    anchor: {
      from: { row: index + 1, col: 7, rowOffsetPx: 5, colOffsetPx: 5 },
      extent: { widthPx: 470, heightPx: 208 },
    },
  });
}

sheet.tables.add("A1:I43", true, "AlpineStyleSceneResults");

await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const sourcePreview = await workbook.render({
  sheetName: "画风素材库",
  range: "A1:G43",
  scale: 0.35,
  format: "png",
});
await fs.writeFile(
  path.join(previewDir, "画风素材库.png"),
  new Uint8Array(await sourcePreview.arrayBuffer()),
);
const resultPreview = await workbook.render({
  sheetName: "阿尔卑斯生成结果",
  range: "A1:I43",
  scale: 0.25,
  format: "png",
});
await fs.writeFile(
  path.join(previewDir, "阿尔卑斯生成结果.png"),
  new Uint8Array(await resultPreview.arrayBuffer()),
);

const tableCheck = await workbook.inspect({
  kind: "table",
  sheetId: "阿尔卑斯生成结果",
  range: "A1:I5",
  include: "values,formulas",
  tableMaxRows: 5,
  tableMaxCols: 9,
  maxChars: 5000,
});
const errorCheck = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
const drawingCheck = await workbook.inspect({
  kind: "drawing",
  sheetId: "阿尔卑斯生成结果",
  maxChars: 50000,
});

console.log(`OUTPUT=${outputPath}`);
console.log(`TABLE_CHECK=${tableCheck.ndjson}`);
console.log(`ERROR_CHECK=${errorCheck.ndjson}`);
console.log(`DRAWING_COUNT=${drawingCheck.ndjson.split("\n").filter(Boolean).length}`);
