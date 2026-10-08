import {ErrorToast} from "../../ui/ErrorToast";
import {errorText,type Operation,type Snapshot} from "./api";
import {useMessageCopy} from "./useMessageCopy";

/** A failed operation is reported as a snackbar (with the edit-and-retry action when it applies), never inline in the conversation. */
export function OperationFailureNotice({operation,messages,stateText,canRecover,onRecover}:{
 operation:Operation;messages:Snapshot["messages"];stateText:string;canRecover:boolean;onRecover:(id:string)=>void;
}){
 const {c,language}=useMessageCopy();
 const reviewFailed=operation.state==="failed"&&["moderation_unavailable","moderation_rejected","prompt_injection_rejected"].includes(operation.error_code??"");
 const source=reviewFailed?messages.find(message=>message.role==="user"&&message.operation_id===operation.id&&message.can_edit):undefined;
 const text=(operation.error_code?errorText(operation.error_code,language):stateText)+(reviewFailed&&operation.settled_units===0?" "+c("reviewNoCharge"):"");
 return <ErrorToast message={text} action={source&&canRecover?{label:c("reviewRecover"),onClick:()=>onRecover(source.id)}:undefined}/>;
}
