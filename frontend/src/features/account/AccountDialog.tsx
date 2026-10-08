import {lazy,Suspense,useState} from "react";
import {ErrorToast} from "../../ui/ErrorToast";
import {useMutation} from "@tanstack/react-query";
import {useTranslation} from "react-i18next";
import {Link} from "react-router-dom";
import {V2Dialog} from "../../ui/V2Dialog";
import {useAuth} from "../auth/AuthProvider";
import {revokeOtherSessions} from "../../api/client";
import QuotaPanel from "./QuotaPanel";
import type {LegalDocument} from "../legal/LegalDocumentsDialog";
import "./account.css";
const Password=lazy(()=>import("../auth/ChangePasswordDialog").then(m=>({default:m.ChangePasswordDialog})));
const Privacy=lazy(()=>import("../legal/PrivacySettingsDialog").then(m=>({default:m.PrivacySettingsDialog})));
const Legal=lazy(()=>import("../legal/LegalDocumentsDialog").then(m=>({default:m.LegalDocumentsDialog})));
const About=lazy(()=>import("../about/AboutPanel"));
type View="account"|"password"|"privacy-settings"|"about"|"sessions"|LegalDocument;
export default function AccountDialog({onClose,onLogout,initialView="account"}:{onClose:()=>void;onLogout:()=>void;initialView?:"account"|"about"}) {
 const {t}=useTranslation();
 const auth=useAuth(),user=auth.currentUser;
 const [view,setView]=useState<View>(initialView);
 const revoke=useMutation({mutationFn:revokeOtherSessions,retry:false});
 const back=()=>setView("account");
 if(!user)return null;
 const titleKey="v2Account."+(view==="about"?"about":view==="sessions"?"revoke":"title");
 return <Suspense fallback={<V2Dialog title={t("v2Account.title")} onClose={onClose} actions={<button onClick={onClose}>{t("legal.close")}</button>}><p role="status">{t("v2Account.loading")}</p></V2Dialog>}>
  {view==="password"?<Password onClose={back}/>:view==="privacy-settings"?<Privacy currentUser={user}
   authSessionEpoch={auth.authSessionEpoch} onUpdated={auth.updateCurrentUserForAuthSession} onClose={back}/>
  :(["terms","privacy","cookies"] as string[]).includes(view)?<Legal document={view as LegalDocument} open onClose={back}/>
  :<V2Dialog size={view==="about"?"lg":"md"} className="v2-account-body" labelId="v2-account-title" title={t(titleKey)} onClose={onClose}
   actions={view==="account"?<button onClick={()=>{auth.logout();onLogout();}}>{t("auth.logoutMenu")}</button>:<button onClick={back}>{t("common.back")}</button>}>
    {view==="about"?<About/>:view==="sessions"?<>
     <p>{t("v2Account.revokeNote")}</p>
     <ErrorToast message={revoke.isError?t("v2Account.failed"):null}/>
     {revoke.isSuccess?<p role="status">{t("v2Account.revoked",{count:revoke.data.revoked_sessions})}</p>
      :<button className="v2-primary" disabled={revoke.isPending} onClick={()=>revoke.mutate()}>{t("v2Account.revokeConfirm")}</button>}
    </>:<>
     <p className="v2-account-address">{user.email}</p>
     <QuotaPanel ownerId={user.id} epoch={auth.authSessionEpoch}/>
     <nav className="v2-account-links" aria-label={t("v2Account.title")}>
      <button onClick={()=>setView("password")}>{t("auth.changePassword.menu")}</button>
      <button onClick={()=>{revoke.reset();setView("sessions");}}>{t("v2Account.revoke")}</button>
      <button onClick={()=>setView("privacy-settings")}>{t("legal.privacySettings.title")}</button>
      {(["terms","privacy","cookies"] as const).map(doc=><button key={doc} onClick={()=>setView(doc)}>{t("legal.documents."+doc+".title")}</button>)}
      <button onClick={()=>setView("about")}>{t("v2Account.about")}</button>
      {user.role==="admin"?<Link to="/admin" onClick={onClose}>{t("auth.adminDashboard")}</Link>:null}
     </nav>
    </>}
  </V2Dialog>}
 </Suspense>;
}
