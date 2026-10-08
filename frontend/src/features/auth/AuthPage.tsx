import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import {
  clearAuthFormDraft,
  createInitialAuthFormState,
  type AuthMode,
  type LoginMethod,
} from "./authFormDraft";
import { useAuthFormDraftPersistence } from "./useAuthFormDraftPersistence";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  ApiError,
  getAuthCaptchaConfig,
  getCurrentTermsVersion,
  loginWithCode,
  loginWithPassword,
  registerAccount,
  resetPassword,
  sendAuthCode,
  type AuthPurpose,
  type CurrentUser,
} from "../../api/client";
import { Alert, Box, Button, Group, Paper, Stack, Tab, Tabs, Text, TextInput, Title } from "../../ui/mui";
import { TurnstileCaptcha } from "./TurnstileCaptcha";
import { useAuth, type AuthConfirmation } from "./AuthProvider";
import { LegalDocumentsDialog, type LegalDocument } from "../legal/LegalDocumentsDialog";

const AUTH_ERROR_KEYS = {
  email_already_registered: "errors.email_already_registered",
  account_not_found: "errors.account_not_found",
  invalid_email_or_password: "errors.invalid_email_or_password",
  verification_code_invalid: "errors.verification_code_invalid",
  account_unavailable: "errors.account_unavailable",
} as const;

type AuthErrorDetail = keyof typeof AUTH_ERROR_KEYS;

const AUTH_PASSWORD_MIN_LENGTH = 8;

function formatAuthError(
  error: unknown,
  fallback: string,
  t: (key: string, options?: Record<string, string | number>) => string,
) {
  if (error instanceof ApiError && error.detail === "verification_code_rate_limited") {
    const waitTime =
      error.retryAfterSeconds && error.retryAfterSeconds < 60
        ? t("errors.wait_seconds", { count: error.retryAfterSeconds })
        : error.retryAfterSeconds
          ? t("errors.wait_minutes", { count: Math.max(1, Math.ceil(error.retryAfterSeconds / 60)) })
          : t("errors.wait_minutes", { count: 1 });
    return t("errors.verification_code_rate_limited", { waitTime });
  }
  if (error instanceof ApiError && (error.detail === "captcha_required" || error.detail === "captcha_invalid")) {
    return t("auth.captchaRequired");
  }
  if (error instanceof ApiError && error.detail === "terms_version_outdated") {
    return t("auth.termsVersionOutdated");
  }
  if (error instanceof ApiError && typeof error.detail === "string") {
    if (Object.hasOwn(AUTH_ERROR_KEYS, error.detail)) {
      const translationKey = AUTH_ERROR_KEYS[error.detail as AuthErrorDetail];
      return t(translationKey);
    }
  }
  return fallback;
}

type AuthPageProps = {
  onAuthenticated?: (confirmation: AuthConfirmation) => void;
  subtitle?: string;
  variant?: "dialog" | "page";
};

export function AuthPage({ onAuthenticated, subtitle, variant = "page" }: AuthPageProps) {
  const { t } = useTranslation();
  const { refreshAuth, isCurrentAuthSession, authSessionEpoch } = useAuth();
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  const [initialState] = useState(() => createInitialAuthFormState());
  const [mode, setMode] = useState<AuthMode>(initialState.mode);
  const [loginMethod, setLoginMethod] = useState<LoginMethod>(initialState.loginMethod);
  const [email, setEmail] = useState(initialState.email);
  const [password, setPassword] = useState(initialState.password);
  const [code, setCode] = useState(initialState.code);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sendCooldownSeconds, setSendCooldownSeconds] = useState(initialState.sendCooldownSeconds);
  const [sendCooldownEndsAt, setSendCooldownEndsAt] = useState<number | null>(
    initialState.sendCooldownEndsAt,
  );
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const [captchaResetSignal, setCaptchaResetSignal] = useState(0);
  const [termsAccepted, setTermsAccepted] = useState(initialState.termsAccepted);
  const [legalDocument, setLegalDocument] = useState<LegalDocument | null>(null);

  useAuthFormDraftPersistence(
    {
      mode,
      loginMethod,
      email,
      password,
      code,
      termsAccepted,
      sendCooldownEndsAt,
      sendCooldownSeconds,
    },
    {
      setMode,
      setLoginMethod,
      setEmail,
      setPassword,
      setCode,
      setTermsAccepted,
      setSendCooldownSeconds,
      setSendCooldownEndsAt,
    },
  );

  const captchaConfigQuery = useQuery({
    queryKey: ["auth", "captcha"],
    queryFn: getAuthCaptchaConfig,
    staleTime: 5 * 60 * 1000,
  });
  const termsVersionQuery = useQuery({
    queryKey: ["auth", "terms-version"],
    queryFn: getCurrentTermsVersion,
    retry: false,
  });

  useEffect(() => {
    if (sendCooldownSeconds <= 0) return;
    const timeoutId = window.setTimeout(() => {
      setSendCooldownSeconds((current) => {
        const next = Math.max(0, current - 1);
        if (next === 0) {
          setSendCooldownEndsAt(null);
        }
        return next;
      });
    }, 1000);
    return () => window.clearTimeout(timeoutId);
  }, [sendCooldownSeconds]);

  const completeAuthenticatedAction = async (user: CurrentUser, _variables: unknown, requestEpoch: number | undefined) => {
    if (!live.current) return;
    const confirmation = await refreshAuth(requestEpoch);
    if (!live.current || !confirmation || confirmation.user.id !== user.id || !isCurrentAuthSession(confirmation)) return;
    setMessage(null);
    clearAuthFormDraft();
    onAuthenticated?.(confirmation);
  };

  const passwordLoginMutation = useMutation({
    mutationFn: loginWithPassword,
    onMutate: () => authSessionEpoch,
    onSuccess: completeAuthenticatedAction,
  });
  const codeLoginMutation = useMutation({
    mutationFn: loginWithCode,
    onMutate: () => authSessionEpoch,
    onSuccess: completeAuthenticatedAction,
  });
  const registerMutation = useMutation({
    mutationFn: registerAccount,
    onMutate: () => authSessionEpoch,
    onSuccess: completeAuthenticatedAction,
  });
  const resetPasswordMutation = useMutation({
    mutationFn: resetPassword,
    onSuccess: () => {
      setMessage(t("auth.passwordResetSuccess"));
      setMode("login");
      setLoginMethod("password");
    },
  });
  const sendCodeMutation = useMutation({
    mutationFn: sendAuthCode,
    onSuccess: () => setMessage(t("auth.codeSent")),
  });

  const isCodeVisible = mode !== "login" || loginMethod === "code";
  const isPasswordVisible = mode !== "login" || loginMethod === "password";
  const submitLabel =
    mode === "login" ? t("auth.login") : mode === "register" ? t("auth.register") : t("auth.resetPassword");
  const isSubmitting =
    passwordLoginMutation.isPending ||
    codeLoginMutation.isPending ||
    registerMutation.isPending ||
    resetPasswordMutation.isPending;
  const isPasswordMode = mode === "register" || mode === "reset";
  const isPasswordTooShort =
    isPasswordMode && password.length > 0 && Array.from(password).length < AUTH_PASSWORD_MIN_LENGTH;

  function resetFeedback() {
    setError(null);
    setMessage(null);
  }

  function resetCaptcha() {
    setCaptchaToken(null);
    setCaptchaResetSignal((current) => current + 1);
  }

  function getCaptchaTokenOrSetError() {
    if (!isCaptchaRequired) return null;
    if (captchaToken) return captchaToken;
    setError(t("auth.captchaRequired"));
    return undefined;
  }

  function handleModeChange(nextMode: AuthMode) {
    setMode(nextMode);
    setCode("");
    resetCaptcha();
    resetFeedback();
    if (nextMode !== "register") setTermsAccepted(false);
    if (nextMode !== "login") setLoginMethod("password");
  }

  async function handleSendCode() {
    resetFeedback();
    const token = getCaptchaTokenOrSetError();
    if (token === undefined) return;
    const purpose: AuthPurpose = mode === "register" ? "register" : mode === "reset" ? "reset_password" : "login";
    try {
      await sendCodeMutation.mutateAsync({ email, purpose, captchaToken: token });
      const cooldownSeconds = 60;
      setSendCooldownSeconds(cooldownSeconds);
      setSendCooldownEndsAt(Date.now() + cooldownSeconds * 1000);
    } catch (sendError) {
      if (sendError instanceof ApiError && sendError.retryAfterSeconds) {
        setSendCooldownSeconds(sendError.retryAfterSeconds);
        setSendCooldownEndsAt(Date.now() + sendError.retryAfterSeconds * 1000);
      }
      setError(formatAuthError(sendError, t("auth.requestFailed"), t));
    } finally {
      resetCaptcha();
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    resetFeedback();
    if (mode === "register" && !termsAccepted) return;
    if (isPasswordTooShort) {
      setError(t("auth.passwordTooShort"));
      return;
    }
    const token = getCaptchaTokenOrSetError();
    if (token === undefined) return;
    try {
      if (mode === "register") {
        await registerMutation.mutateAsync({
          email,
          code,
          password,
          termsAccepted: true,
          termsVersion: termsVersionQuery.data?.version ?? "",
          captchaToken: token,
        });
      } else if (mode === "reset") {
        await resetPasswordMutation.mutateAsync({ email, code, password, captchaToken: token });
      } else if (loginMethod === "code") {
        await codeLoginMutation.mutateAsync({ email, code, captchaToken: token });
      } else {
        await passwordLoginMutation.mutateAsync({ email, password, captchaToken: token });
      }
    } catch (submitError) {
      if (mode === "register" && submitError instanceof ApiError && submitError.detail === "terms_version_outdated") {
        setTermsAccepted(false);
        void termsVersionQuery.refetch();
      }
      setError(formatAuthError(submitError, t("auth.requestFailed"), t));
    } finally {
      resetCaptcha();
    }
  }

  const resolvedSubtitle =
    subtitle ?? (variant === "dialog" ? t("auth.subtitleDialog") : t("auth.subtitlePage"));
  const captchaConfig = captchaConfigQuery.data;
  const captchaSiteKey = captchaConfig?.site_key ?? "";
  const isCaptchaRequired = Boolean(captchaConfig?.enabled && captchaSiteKey);
  const isCaptchaLoading = captchaConfigQuery.isLoading;
  const isTermsVersionLoading = termsVersionQuery.isLoading;
  const isSubmitDisabled =
    isCaptchaLoading ||
    (isCaptchaRequired && !captchaToken) ||
    isPasswordTooShort ||
    (mode === "register" && (!termsAccepted || isTermsVersionLoading || !termsVersionQuery.data?.version));
  const isSendCodeDisabled =
    !email || sendCooldownSeconds > 0 || isCaptchaLoading || (isCaptchaRequired && !captchaToken);

  return (
    <Box
      mih={variant === "page" ? "100vh" : undefined}
      px={{ base: 2, sm: 3 }}
      py={variant === "page" ? { base: 4, sm: 6 } : 0}
      style={variant === "page" ? { display: "grid", placeItems: "center" } : undefined}
    >
      <Paper withBorder shadow="md" p={{ base: 2, sm: 3 }} radius={0} maw={440} w="100%">
        <Stack gap={2} component="form" onSubmit={handleSubmit}>
          <Stack gap={0.5}>
            <Title order={1} size="h4">KigCraft</Title>
            <Text c="dimmed" size="sm">{resolvedSubtitle}</Text>
          </Stack>

          <Tabs value={mode} onChange={(_, value) => handleModeChange(value as AuthMode)}>
            <Tab label={t("auth.login")} value="login" />
            <Tab label={t("auth.register")} value="register" />
            <Tab label={t("auth.resetPassword")} value="reset" />
          </Tabs>

          {mode === "login" ? (
            <Group gap={1} grow>
              <Button
                type="button"
                variant={loginMethod === "password" ? "filled" : "default"}
                onClick={() => {
                  setLoginMethod("password");
                  setCode("");
                  resetFeedback();
                }}
              >
                {t("auth.passwordLogin")}
              </Button>
              <Button
                type="button"
                variant={loginMethod === "code" ? "filled" : "default"}
                onClick={() => {
                  setLoginMethod("code");
                  setPassword("");
                  resetFeedback();
                }}
              >
                {t("auth.codeLogin")}
              </Button>
            </Group>
          ) : null}

          {error ? <Alert color="red" variant="light">{error}</Alert> : null}
          {message ? <Alert color="green" variant="light">{message}</Alert> : null}

          <TextInput
            autoComplete="email"
            label={t("auth.email")}
            required
            type="email"
            value={email}
            onChange={(event: ChangeEvent<HTMLInputElement>) => setEmail(event.currentTarget.value)}
          />

          {isCodeVisible ? (
            <Group gap={1} align="flex-start">
              <TextInput
                autoComplete="one-time-code"
                label={t("auth.verificationCode")}
                required
                value={code}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setCode(event.currentTarget.value)}
              />
              <Button
                disabled={isSendCodeDisabled}
                loading={sendCodeMutation.isPending}
                mih={48}
                type="button"
                variant="default"
                onClick={() => void handleSendCode()}
              >
                {sendCooldownSeconds > 0
                  ? t("auth.sendCodeCooldown", { seconds: sendCooldownSeconds })
                  : t("auth.sendCode")}
              </Button>
            </Group>
          ) : null}

          {isPasswordVisible ? (
            <TextInput
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              error={isPasswordTooShort}
              helperText={isPasswordMode ? t("auth.passwordTooShort") : undefined}
              label={t("auth.password")}
              required
              type="password"
              value={password}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setPassword(event.currentTarget.value)}
            />
          ) : null}

          {mode === "register" ? (
            <Stack gap={0.5}>
              <Box component="label" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  checked={termsAccepted}
                  onChange={(event: ChangeEvent<HTMLInputElement>) => setTermsAccepted(event.currentTarget.checked)}
                  type="checkbox"
                />
                <Text component="span" size="sm">{t("auth.acceptTerms")}</Text>
              </Box>
              <Group gap={1} wrap="wrap">
                <Button onClick={() => setLegalDocument("terms")} size="sm" type="button" variant="subtle">
                  {t("legal.documents.terms.title")}
                </Button>
                <Button onClick={() => setLegalDocument("privacy")} size="sm" type="button" variant="subtle">
                  {t("legal.documents.privacy.title")}
                </Button>
              </Group>
            </Stack>
          ) : null}

          {isCaptchaRequired ? (
            <TurnstileCaptcha
              resetSignal={captchaResetSignal}
              siteKey={captchaSiteKey}
              onTokenChange={setCaptchaToken}
            />
          ) : null}

          <Button fullWidth disabled={isSubmitDisabled} loading={isSubmitting} type="submit" variant="filled">
            {submitLabel}
          </Button>
        </Stack>
      </Paper>
      <LegalDocumentsDialog
        document={legalDocument ?? "terms"}
        onClose={() => setLegalDocument(null)}
        open={legalDocument !== null}
      />
    </Box>
  );
}
