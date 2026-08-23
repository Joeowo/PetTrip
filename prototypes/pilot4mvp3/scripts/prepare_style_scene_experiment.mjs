import fs from "node:fs/promises";
import path from "node:path";

import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve(import.meta.dirname, "..");
const sourceWorkbook = path.resolve(root, "..", "docs", "画风素材库_42条.xlsx");
const extractedMediaDir = path.join(root, ".xlsx-build", "source-unpacked", "xl", "media");
const experimentDir = path.join(root, "style-scene-experiment-20260819");
const referencesDir = path.join(experimentDir, "references");
const promptsDir = path.join(experimentDir, "prompts");
const outputsDir = path.join(experimentDir, "images");

function promptFor({ styleName, gameName, keywords, detail }) {
  return `Use case: stylized-concept
Asset type: 16:9 horizontal game travel scene, fixed camera
Input images: Images 1, 2, and 3 are visual-style references only. Derive their medium, mark-making, edge treatment, shape language, material treatment, color relationships, lighting logic, and level of detail. Do not copy their specific characters, objects, text, logos, UI, layout, or events.
Primary request: Generate one pure environment scene of an alpine valley with small timber chalets. The foreground is a broad grassy sloped ground strip with a few wild grasses. The midground contains several small timber chalets on the lower left and a winding path leading toward a mountain pass. The background contains one huge gray alpine peak on the right, layered blue-green valleys, and soft clouds. Keep the chalet path and the safe grass slope before the mountain foot clearly readable as two separate landmarks.
Target style name: ${styleName}
Style source label: ${gameName}
Style keywords: ${keywords}
Style direction: ${detail}
Composition/framing: cinematic horizontal composition, fixed eye-level lens, clear foreground/midground/background, strong scale contrast between the tiny chalets and the large mountain, usable standing ground near both landmarks, no extreme perspective.
Lighting/mood: calm, inviting alpine daylight with one coherent key-light direction; adapt the rendering of light to the target style while preserving scene readability.
Constraints: apply the target style consistently to the entire scene; exactly one alpine valley scene; no character, no person, no animal, no pet, no UI, no text, no letters, no numbers, no logo, no watermark, no extra building cluster, no copied composition from the reference images.`;
}

await fs.mkdir(referencesDir, { recursive: true });
await fs.mkdir(promptsDir, { recursive: true });
await fs.mkdir(outputsDir, { recursive: true });

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(sourceWorkbook));
const sheet = workbook.worksheets.getItem("画风素材库");
const values = sheet.getRange("A2:D43").values;
const manifest = [];

for (let index = 0; index < values.length; index += 1) {
  const [styleName, gameName, keywords, detail] = values[index].map((value) => String(value ?? "").trim());
  if (!styleName || !gameName || !keywords || !detail) {
    throw new Error(`Source row ${index + 2} has an empty required field`);
  }
  const id = `S${String(index + 1).padStart(2, "0")}`;
  const refs = [];
  for (let refIndex = 0; refIndex < 3; refIndex += 1) {
    const mediaNumber = index * 3 + refIndex + 1;
    const source = path.join(extractedMediaDir, `image${mediaNumber}.png`);
    const destination = path.join(referencesDir, `${id}-ref-${refIndex + 1}.png`);
    await fs.copyFile(source, destination);
    refs.push(path.relative(root, destination).replaceAll("\\", "/"));
  }
  const prompt = promptFor({ styleName, gameName, keywords, detail });
  const promptPath = path.join(promptsDir, `${id}.txt`);
  await fs.writeFile(promptPath, prompt, "utf8");
  manifest.push({
    id,
    sourceRow: index + 2,
    styleName,
    gameName,
    keywords,
    detail,
    scene: "阿尔卑斯山谷小屋",
    references: refs,
    prompt: path.relative(root, promptPath).replaceAll("\\", "/"),
    output: path.relative(root, path.join(outputsDir, `${id}.png`)).replaceAll("\\", "/"),
    model: "gpt-image-2",
    size: "1376x768",
    quality: "high",
  });
}

await fs.writeFile(
  path.join(experimentDir, "manifest.json"),
  JSON.stringify({ sourceWorkbook, records: manifest }, null, 2),
  "utf8",
);
console.log(`Prepared ${manifest.length} style-scene jobs in ${experimentDir}`);
