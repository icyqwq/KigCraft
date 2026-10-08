export type AuthMode = "login" | "register" | "reset";
export type LoginMethod = "password" | "code";

export type AuthFormDraft = {
  mode: AuthMode;
  loginMethod: LoginMethod;
  email: string;
  password: string;
  code: string;
  termsAccepted: boolean;
  sendCooldownEndsAt: number | null;
};

const FORM_STORAGE_KEY = "kig-auth-form-draft";
const DIALOG_OPEN_STORAGE_KEY = "kig-auth-dialog-open";

function isAuthMode(value: unknown): value is AuthMode {
  return value === "login" || value === "register" || value === "reset";
}

function isLoginMethod(value: unknown): value is LoginMethod {
  return value === "password" || value === "code";
}

export function readAuthFormDraft(): AuthFormDraft | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(FORM_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<AuthFormDraft>;
    if (!isAuthMode(parsed.mode) || !isLoginMethod(parsed.loginMethod)) return null;
    return {
      mode: parsed.mode,
      loginMethod: parsed.loginMethod,
      email: typeof parsed.email === "string" ? parsed.email : "",
      password: typeof parsed.password === "string" ? parsed.password : "",
      code: typeof parsed.code === "string" ? parsed.code : "",
      termsAccepted: parsed.termsAccepted === true,
      sendCooldownEndsAt:
        typeof parsed.sendCooldownEndsAt === "number" ? parsed.sendCooldownEndsAt : null,
    };
  } catch {
    return null;
  }
}

export function writeAuthFormDraft(draft: AuthFormDraft): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(FORM_STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // Ignore quota or privacy-mode failures; the in-memory form still works.
  }
}

export function readAuthDialogOpen(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.sessionStorage.getItem(DIALOG_OPEN_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function writeAuthDialogOpen(open: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(DIALOG_OPEN_STORAGE_KEY, open ? "true" : "false");
  } catch {
    // Ignore storage failures.
  }
}

export function clearAuthFormDraft(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(FORM_STORAGE_KEY);
    window.sessionStorage.removeItem(DIALOG_OPEN_STORAGE_KEY);
  } catch {
    // Ignore storage failures.
  }
}

export function getRemainingSendCooldownSeconds(sendCooldownEndsAt: number | null): number {
  if (!sendCooldownEndsAt) return 0;
  return Math.max(0, Math.ceil((sendCooldownEndsAt - Date.now()) / 1000));
}

export function createInitialAuthFormState(): {
  mode: AuthMode;
  loginMethod: LoginMethod;
  email: string;
  password: string;
  code: string;
  termsAccepted: boolean;
  sendCooldownSeconds: number;
  sendCooldownEndsAt: number | null;
} {
  const draft = readAuthFormDraft();
  const sendCooldownEndsAt = draft?.sendCooldownEndsAt ?? null;
  return {
    mode: draft?.mode ?? "login",
    loginMethod: draft?.loginMethod ?? "password",
    email: draft?.email ?? "",
    password: draft?.password ?? "",
    code: draft?.code ?? "",
    termsAccepted: draft?.termsAccepted ?? false,
    sendCooldownSeconds: getRemainingSendCooldownSeconds(sendCooldownEndsAt),
    sendCooldownEndsAt,
  };
}
