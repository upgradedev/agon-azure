/*
  SHA-256 in the visitor's browser with WebCrypto. The Verify page hashes the
  exact bytes it receives, never a decoded and re-encoded copy.
*/
import { looksLikeHtml, toHex } from "./core";

/* SHA-256 of "abc", FIPS 180-2 appendix B.1: the known-answer test. */
export const SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

export function hasSubtle(): boolean {
  return typeof crypto !== "undefined" && typeof crypto.subtle !== "undefined" && typeof TextEncoder !== "undefined";
}

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", data));
}

export type SelfTest = { ok: true; message: string } | { ok: false; message: string };

export async function selfTest(): Promise<SelfTest> {
  if (!hasSubtle()) {
    return {
      ok: false,
      message: "This browser cannot compute SHA-256 on this page. WebCrypto needs a secure https page, so nothing can be recomputed here.",
    };
  }
  try {
    const bytes = new TextEncoder().encode("abc");
    const hex = await sha256Hex(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
    return hex === SHA256_ABC
      ? { ok: true, message: "This browser computed the standard SHA-256 test vector correctly, so the hashes below are computed on your device." }
      : { ok: false, message: "This browser returned a wrong SHA-256 result for the standard test vector, so its hashes could not be trusted." };
  } catch {
    return { ok: false, message: "This browser refused to compute SHA-256 on this page, so nothing can be recomputed here." };
  }
}

export interface FetchedBytes {
  buffer: ArrayBuffer;
  text: string;
}

export async function fetchBytes(url: string): Promise<FetchedBytes> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${url} answered ${response.status}`);
  }
  const buffer = await response.arrayBuffer();
  const text = new TextDecoder("utf-8").decode(new Uint8Array(buffer));
  if (looksLikeHtml(text)) {
    throw new Error(`${url} returned a web page instead of the file`);
  }
  return { buffer, text };
}
