import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import {
  readAuthFormDraft,
  writeAuthFormDraft,
  type AuthFormDraft,
  type AuthMode,
  type LoginMethod,
} from "./authFormDraft";

type AuthFormState = AuthFormDraft & {
  sendCooldownSeconds: number;
};

type AuthFormSetters = {
  setMode: Dispatch<SetStateAction<AuthMode>>;
  setLoginMethod: Dispatch<SetStateAction<LoginMethod>>;
  setEmail: Dispatch<SetStateAction<string>>;
  setPassword: Dispatch<SetStateAction<string>>;
  setCode: Dispatch<SetStateAction<string>>;
  setTermsAccepted: Dispatch<SetStateAction<boolean>>;
  setSendCooldownSeconds: Dispatch<SetStateAction<number>>;
  setSendCooldownEndsAt: Dispatch<SetStateAction<number | null>>;
};

function applyAuthFormDraft(draft: AuthFormDraft, setters: AuthFormSetters) {
  setters.setMode(draft.mode);
  setters.setLoginMethod(draft.loginMethod);
  setters.setEmail(draft.email);
  setters.setPassword(draft.password);
  setters.setCode(draft.code);
  setters.setTermsAccepted(draft.termsAccepted);
  setters.setSendCooldownEndsAt(draft.sendCooldownEndsAt);
  setters.setSendCooldownSeconds(
    draft.sendCooldownEndsAt
      ? Math.max(0, Math.ceil((draft.sendCooldownEndsAt - Date.now()) / 1000))
      : 0,
  );
}

export function useAuthFormDraftPersistence(state: AuthFormState, setters: AuthFormSetters) {
  const settersRef = useRef(setters);
  settersRef.current = setters;
  const draftRef = useRef<AuthFormDraft>({
    mode: state.mode,
    loginMethod: state.loginMethod,
    email: state.email,
    password: state.password,
    code: state.code,
    termsAccepted: state.termsAccepted,
    sendCooldownEndsAt: state.sendCooldownEndsAt,
  });

  draftRef.current = {
    mode: state.mode,
    loginMethod: state.loginMethod,
    email: state.email,
    password: state.password,
    code: state.code,
    termsAccepted: state.termsAccepted,
    sendCooldownEndsAt: state.sendCooldownEndsAt,
  };

  useEffect(() => {
    writeAuthFormDraft(draftRef.current);
  }, [
    state.mode,
    state.loginMethod,
    state.email,
    state.password,
    state.code,
    state.termsAccepted,
    state.sendCooldownEndsAt,
  ]);

  useEffect(() => {
    const flushDraft = () => {
      writeAuthFormDraft(draftRef.current);
    };
    const restoreDraft = () => {
      const draft = readAuthFormDraft();
      if (draft) {
        applyAuthFormDraft(draft, settersRef.current);
      }
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        flushDraft();
        return;
      }
      restoreDraft();
    };

    window.addEventListener("pagehide", flushDraft);
    window.addEventListener("pageshow", restoreDraft);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("pagehide", flushDraft);
      window.removeEventListener("pageshow", restoreDraft);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);
}
