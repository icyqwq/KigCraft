export type ReferenceAttachment = { key: string; usage: string; assetId?: string; file?: File };
export type ReferenceOption = { key: string; label: string; referenceNumber?: number; description?: string; imageUrl?: string;
  /** Where a picker tile comes from: an earlier reference upload or a generated version of the design. */
  kind?: "reference" | "version"; versionNumber?: number };
export const referenceFileTypes = ["image/png","image/jpeg","image/webp"];
export class ReferenceSelectionError extends Error {
  constructor(public reason: "limit" | "invalid", public maximum: number, public files: string[] = []) {
    super(reason === "limit" ? "最多选择 "+maximum+" 张参考图，已保留原选择。" :
      "未添加 "+files.join("、")+"。请选择不超过 20 MB 的 PNG、JPEG 或 WebP 图片。");
    this.name = "ReferenceSelectionError";
  }
}
export function appendReferenceFiles(current: ReferenceAttachment[], files: Iterable<File>, maximum: number) {
  const selected = Array.from(files);
  if (current.length + selected.length > maximum) throw new ReferenceSelectionError("limit",maximum);
  const invalid = selected.filter(file=>!referenceFileTypes.includes(file.type) || file.size > 20*1024*1024 || !file.size);
  if (invalid.length) throw new ReferenceSelectionError("invalid",maximum,invalid.map(file=>file.name));
  return [...current,...selected.map(file=>({key:crypto.randomUUID(),usage:"",file}))];
}
export class ReferenceUploadFailure extends Error {
  constructor(public key:string, public file:File, public original:unknown) { super("参考图 "+file.name+" 上传未完成。"); }
}
/** Scoped to one owner/editor. Successful uploads survive a later attachment failure. */
export class ReferenceUploader {
  private assets = new WeakMap<Blob,string>();
  async blob(file: Blob, upload: (file: Blob)=>Promise<{id:string}>) {
    const cached = this.assets.get(file);
    if (cached) return cached;
    const asset = await upload(file); this.assets.set(file,asset.id); return asset.id;
  }
  async prepare(items: readonly ReferenceAttachment[], upload: (file: Blob)=>Promise<{id:string}>,
      onUploaded?: (item: ReferenceAttachment)=>void) {
    const ready: ReferenceAttachment[]=[];
    for (const input of items) {
      const item={...input};
      if (!item.assetId) {
        if (!item.file) throw new Error("参考图文件不可用，请重新选择。");
        try { item.assetId=await this.blob(item.file,upload); }
        catch(error) { throw new ReferenceUploadFailure(item.key,item.file,error); }
        onUploaded?.(item);
      }
      ready.push(item);
    }
    return ready;
  }
}
