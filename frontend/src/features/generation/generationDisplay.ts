import {
  resolveGenerationStatusLabel,
  resolveUserMessage,
} from "./resolveUserMessage";

export function formatGenerationStatusLabel(status: string) {
  return resolveGenerationStatusLabel(status);
}

export function formatGenerationMessage(value: unknown) {
  return resolveUserMessage(value);
}

export function shouldShowGenerationEvent(type: string) {
  return type !== "token_usage";
}
