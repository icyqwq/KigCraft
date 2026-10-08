import {useQuery} from "@tanstack/react-query";
import {ErrorToast} from "../../ui/ErrorToast";
import {useTranslation} from "react-i18next";
import {designApi} from "../design/api";
export function formatAccountTime(value:string,locale:string) {
 const date=new Date(value);
 return Number.isNaN(date.getTime())?"—":new Intl.DateTimeFormat(locale,{
  year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",timeZoneName:"short",
 }).format(date);
}
const kinds=new Set(["opening","topup","charge","settle","release","refund","legacy_refund","adjust"]);
const delta=(value:number)=>value===0?"—":(value>0?"+":"")+value;
export default function QuotaPanel({ownerId,epoch}:{ownerId:string;epoch:number}) {
 const {t,i18n}=useTranslation();
 const quota=useQuery({queryKey:["v2",ownerId,"quota",epoch],queryFn:designApi.quota,retry:false});
 const data=quota.data;
 const entries=data?.entries.filter(row=>row.balance_delta!==0)??[];
 if(quota.isPending)return <p role="status">{t("v2Account.loading")}</p>;
 if(quota.isError)return <ErrorToast message={t("v2Account.quotaError")} action={{label:t("v2Account.retry"),onClick:()=>void quota.refetch()}}/>;
 if(!data||data.available===null)return <p>{t("v2Account.unavailable")}</p>;
 return <section aria-label={t("v2Account.quota")}>
  <dl className="v2-quota-values">
   <div><dt>{t("v2Account.available")}</dt><dd>{data.available}</dd></div>
  </dl>
  {data.exempt?<p>{t("v2Account.exempt")}</p>:null}
  {data.reset_at?<p>{t("v2Account.reset")} <time dateTime={data.reset_at}>{formatAccountTime(data.reset_at,i18n.language)}</time></p>:null}
  <details className="v2-account-records"><summary>{t("v2Account.records")}</summary>
   {entries.length?<div className="v2-account-table"><table aria-label={t("v2Account.records")}>
    <thead><tr>{["time","event","change"].map(key=><th key={key} scope="col">{t("v2Account."+key)}</th>)}</tr></thead>
    <tbody>{entries.map(row=><tr key={row.id}>
     <td><time dateTime={row.created_at}>{formatAccountTime(row.created_at,i18n.language)}</time></td>
     <td>{t("v2Account.kinds."+(kinds.has(row.kind)?row.kind:"unknown"))}</td>
     <td>{delta(row.balance_delta)}</td>
    </tr>)}</tbody>
   </table></div>:<p>{t("v2Account.empty")}</p>}
  </details>
 </section>;
}
