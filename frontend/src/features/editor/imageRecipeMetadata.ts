import { normalizeEditRecipe, type EditRecipe } from "./deformation/recipe";

const PNG_SIGNATURE = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10]);
const RECIPE_KEYWORD = "kigcraft.edit-recipe";
const RECIPE_SCHEMA = "kigcraft.edit-recipe";
const RECIPE_SCHEMA_VERSION = 1;
const MAX_METADATA_BYTES = 4 * 1024 * 1024;
const textDecoder = new TextDecoder("utf-8", { fatal: true });
const textEncoder = new TextEncoder();

type PngChunk = {
  data: Uint8Array;
  end: number;
  start: number;
  type: string;
};

type StoredEditRecipe = {
  recipe: EditRecipe;
  schema: typeof RECIPE_SCHEMA;
  version: typeof RECIPE_SCHEMA_VERSION;
};

export async function embedEditRecipeInPng(imageBlob: Blob, recipe: EditRecipe): Promise<Blob> {
  const bytes = await readBlobBytes(imageBlob);
  if (!hasPngSignature(bytes)) return imageBlob;

  const chunks = parsePngChunks(bytes);
  if (!chunks || !chunks.some((chunk) => chunk.type === "IEND")) return imageBlob;

  const payload: StoredEditRecipe = {
    recipe: normalizeEditRecipe(recipe),
    schema: RECIPE_SCHEMA,
    version: RECIPE_SCHEMA_VERSION,
  };
  const jsonBytes = textEncoder.encode(JSON.stringify(payload));
  if (jsonBytes.byteLength > MAX_METADATA_BYTES) {
    throw new Error("Edit recipe metadata is too large to save in the image");
  }

  const recipeChunk = createInternationalTextChunk(RECIPE_KEYWORD, jsonBytes);
  const parts: Uint8Array[] = [PNG_SIGNATURE];
  for (const chunk of chunks) {
    if (isRecipeMetadataChunk(chunk)) continue;
    if (chunk.type === "IEND") parts.push(recipeChunk);
    parts.push(bytes.subarray(chunk.start, chunk.end));
  }

  return new Blob([concatenateBytes(parts)], { type: "image/png" });
}

/** Reads an edit record. It does not establish a trusted root image or authorize replay on the PNG. */
export async function readEditRecipeFromImage(imageBlob: Blob): Promise<EditRecipe | null> {
  if (imageBlob.size > 128 * 1024 * 1024) return null;
  try {
    const bytes = await readBlobBytes(imageBlob);
    if (!hasPngSignature(bytes)) return null;

    const chunks = parsePngChunks(bytes);
    if (!chunks) return null;

    let storedRecipe: EditRecipe | null = null;
    for (const chunk of chunks) {
      const jsonBytes = readRecipeJsonBytes(chunk);
      if (!jsonBytes || jsonBytes.byteLength > MAX_METADATA_BYTES) continue;
      if (!hasValidChunkCrc(bytes, chunk)) continue;

      const parsed = JSON.parse(textDecoder.decode(jsonBytes)) as unknown;
      const recipe = parseStoredRecipe(parsed);
      if (recipe) storedRecipe = recipe;
    }
    return storedRecipe;
  } catch {
    return null;
  }
}

function parseStoredRecipe(value: unknown): EditRecipe | null {
  if (!isRecord(value) || value.schema !== RECIPE_SCHEMA || value.version !== RECIPE_SCHEMA_VERSION) return null;
  const recipe = value.recipe;
  if (!isRecord(recipe)) return null;
  if (!isFiniteNumberRecord(recipe.face) || !isFiniteNumberRecord(recipe.eyes) || !isFiniteNumberRecord(recipe.mouth)) {
    return null;
  }
  if (!Array.isArray(recipe.liquify) || !Array.isArray(recipe.annotations) || !isRecord(recipe.details)) return null;

  try {
    return normalizeEditRecipe(recipe as EditRecipe);
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumberRecord(value: unknown) {
  return isRecord(value) && Object.values(value).every((entry) => typeof entry === "number" && Number.isFinite(entry));
}

function hasPngSignature(bytes: Uint8Array) {
  return bytes.byteLength >= PNG_SIGNATURE.byteLength && PNG_SIGNATURE.every((value, index) => bytes[index] === value);
}

function parsePngChunks(bytes: Uint8Array): PngChunk[] | null {
  const chunks: PngChunk[] = [];
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = PNG_SIGNATURE.byteLength;

  while (offset + 12 <= bytes.byteLength) {
    const length = view.getUint32(offset);
    const dataStart = offset + 8;
    const end = dataStart + length + 4;
    if (end > bytes.byteLength) return null;

    const type = String.fromCharCode(bytes[offset + 4], bytes[offset + 5], bytes[offset + 6], bytes[offset + 7]);
    chunks.push({ data: bytes.subarray(dataStart, dataStart + length), end, start: offset, type });
    offset = end;
    if (type === "IEND") return offset === bytes.byteLength ? chunks : null;
  }

  return null;
}

function createInternationalTextChunk(keyword: string, jsonBytes: Uint8Array) {
  const keywordBytes = textEncoder.encode(keyword);
  const data = new Uint8Array(keywordBytes.byteLength + 5 + jsonBytes.byteLength);
  data.set(keywordBytes, 0);
  let offset = keywordBytes.byteLength;
  data[offset++] = 0; // keyword terminator
  data[offset++] = 0; // uncompressed text
  data[offset++] = 0; // compression method
  data[offset++] = 0; // empty language tag
  data[offset++] = 0; // empty translated keyword
  data.set(jsonBytes, offset);
  return createPngChunk("iTXt", data);
}

function createPngChunk(type: string, data: Uint8Array) {
  const typeBytes = textEncoder.encode(type);
  const output = new Uint8Array(12 + data.byteLength);
  const view = new DataView(output.buffer);
  view.setUint32(0, data.byteLength);
  output.set(typeBytes, 4);
  output.set(data, 8);
  view.setUint32(8 + data.byteLength, crc32(concatenateBytes([typeBytes, data])));
  return output;
}

function readRecipeJsonBytes(chunk: PngChunk): Uint8Array | null {
  if (chunk.type === "iTXt") {
    const keywordEnd = chunk.data.indexOf(0);
    if (keywordEnd < 0 || decodeAscii(chunk.data.subarray(0, keywordEnd)) !== RECIPE_KEYWORD) return null;
    let offset = keywordEnd + 1;
    if (offset + 2 > chunk.data.byteLength) return null;
    const compressionFlag = chunk.data[offset++];
    const compressionMethod = chunk.data[offset++];
    if (compressionFlag !== 0 || compressionMethod !== 0) return null;
    const languageEnd = chunk.data.indexOf(0, offset);
    if (languageEnd < 0) return null;
    offset = languageEnd + 1;
    const translatedKeywordEnd = chunk.data.indexOf(0, offset);
    if (translatedKeywordEnd < 0) return null;
    return chunk.data.subarray(translatedKeywordEnd + 1);
  }

  if (chunk.type === "tEXt") {
    const keywordEnd = chunk.data.indexOf(0);
    if (keywordEnd < 0 || decodeAscii(chunk.data.subarray(0, keywordEnd)) !== RECIPE_KEYWORD) return null;
    return chunk.data.subarray(keywordEnd + 1);
  }
  return null;
}

function isRecipeMetadataChunk(chunk: PngChunk) {
  return readRecipeJsonBytes(chunk) !== null;
}

function hasValidChunkCrc(bytes: Uint8Array, chunk: PngChunk) {
  const storedCrc = new DataView(bytes.buffer, bytes.byteOffset + chunk.end - 4, 4).getUint32(0);
  const typeAndData = bytes.subarray(chunk.start + 4, chunk.end - 4);
  return storedCrc === crc32(typeAndData);
}

function decodeAscii(bytes: Uint8Array) {
  return String.fromCharCode(...bytes);
}

function concatenateBytes(parts: readonly Uint8Array[]) {
  const output = new Uint8Array(parts.reduce((total, part) => total + part.byteLength, 0));
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.byteLength;
  }
  return output;
}

function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function readBlobBytes(blob: Blob): Promise<Uint8Array> {
  if (typeof blob.arrayBuffer === "function") {
    return blob.arrayBuffer().then((buffer) => new Uint8Array(buffer));
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("Unable to read image metadata"));
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.readAsArrayBuffer(blob);
  });
}
