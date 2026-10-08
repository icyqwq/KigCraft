/** URL of a file in public/, honouring the base the app is served from (the side-by-side build lives under /v2/). */
export function publicUrl(path: string): string {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  return base + (path.startsWith("/") ? path : "/" + path);
}
