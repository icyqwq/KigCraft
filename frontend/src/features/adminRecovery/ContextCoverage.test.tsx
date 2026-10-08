import {render,screen,cleanup} from "@testing-library/react";
import {afterEach,expect,it} from "vitest";
import "../../i18n";
import {contextRecovery} from "./api";
import {ContextCoverage} from "./ContextCoverage";
afterEach(cleanup);
const empty={attempts:0,confirmed:0,unconfirmed:0,confirmation_rate:null};
const data={scope:"main_session_open",since:null,methods:{start:empty,resume:empty,fork:empty},dispatched_operations_without_observations:0};
it("accepts no observations without inventing a zero or perfect rate",()=>{
 const value=contextRecovery.parse(data);
 expect(value.methods.resume.confirmation_rate).toBeNull();
 render(<ContextCoverage value={value}/>);
 expect(screen.getAllByText(/未知|Unknown|不明/).length).toBeGreaterThan(0);
});
it("rejects inconsistent counts and fabricated percentages",()=>{
 expect(contextRecovery.safeParse({...data,methods:{...data.methods,resume:{attempts:2,confirmed:1,unconfirmed:1,confirmation_rate:1}}}).success).toBe(false);
 expect(contextRecovery.safeParse({...data,methods:{...data.methods,resume:{attempts:2,confirmed:1,unconfirmed:0,confirmation_rate:.5}}}).success).toBe(false);
 expect(contextRecovery.safeParse({...data,methods:{...data.methods,resume:{attempts:2,confirmed:1,unconfirmed:1,confirmation_rate:.5}}}).success).toBe(true);
});
