import {cleanup,fireEvent,render,screen} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import "../../i18n";
import {healthReport} from "./api";
import {HealthSignals} from "./HealthSignals";
afterEach(cleanup);
it("keeps missing telemetry unknown and opens the selected operation",()=>{
 const value=healthReport.parse({checked_at:"2026-09-28T00:00:00Z",stalled_seconds:300,recent_seconds:3600,signals:[
 {code:"expired_leases",status:"active",severity:"warning",count:3,threshold:3,operation_ids:["operation-1"]},
 {code:"access_denials",status:"unknown",severity:"warning",count:null,threshold:1,operation_ids:[]},
 ]});
 const open=vi.fn();render(<HealthSignals value={value} onOperation={open}/>);
 expect(screen.getAllByText(/未知|unknown|不明/).length).toBeGreaterThan(0);
 fireEvent.click(screen.getByRole("button",{name:/operation-1/}));expect(open).toHaveBeenCalledExactlyOnceWith("operation-1");
});
it("renders unavailable health separately from a clear signal",()=>{
 render(<HealthSignals onOperation={()=>{}}/>);
 expect(screen.getAllByText(/未知|Unknown|不明/).length).toBeGreaterThan(0);
});

it("shows incomplete collector coverage even when known incidents already trigger an alert",()=>{
 const value=healthReport.parse({checked_at:"2026-09-28T00:00:00Z",stalled_seconds:300,recent_seconds:3600,signals:[
 {code:"access_denials",status:"active",severity:"critical",count:10,threshold:10,operation_ids:[],
  coverage:{scope:"authenticated_v2_resource_references",status:"incomplete",active_processes:1,expected_processes:2,
   stale_processes:1,gap_recorded:true,inventory_complete:true}},
 ]});
 render(<HealthSignals value={value} onOperation={()=>{}}/>);
 expect(screen.getByText(/正文资源引用|request bodies|本文内/)).toBeInTheDocument();
 expect(screen.getByText(/采集覆盖不完整|Collection coverage is incomplete|収集が一部欠けています/)).toBeInTheDocument();
 expect(screen.getByText(/采集失败|Collection failed|収集が失敗/)).toBeInTheDocument();
 expect(screen.getByText(/活跃采集进程|Active collectors|稼働中の収集プロセス/)).toHaveTextContent("1");
});

it("shows cluster and bucket capacity separately while preserving incomplete data",()=>{
 const value=healthReport.parse({checked_at:"2026-09-28T00:00:00Z",stalled_seconds:300,recent_seconds:3600,signals:[
 {code:"storage_capacity",scope:"minio",status:"active",severity:"critical",count:1,threshold:1,operation_ids:[],
  free_bytes:1048576,minimum_free_bytes:2097152,capacity:{
   cluster:{status:"unknown",free_bytes:null},
   bucket:{status:"active",free_bytes:1048576,quota_bytes:10485760,used_bytes:9437184,age_seconds:1,unlimited:false},
  }},
 ]});
 render(<HealthSignals value={value} onOperation={()=>{}}/>);
 expect(screen.getByText(/MinIO 集群|MinIO cluster|MinIOクラスター/)).toHaveTextContent(/未知|Unknown|不明/);
 expect(screen.getByText(/MinIO 目标桶|MinIO target bucket|MinIO対象バケット/)).toHaveTextContent("MiB");
});
it("distinguishes unlimited bucket quota from missing quota telemetry",()=>{
 const value=healthReport.parse({checked_at:"2026-09-28T00:00:00Z",stalled_seconds:300,recent_seconds:3600,signals:[
 {code:"storage_capacity",scope:"minio",status:"clear",severity:"critical",count:0,threshold:1,operation_ids:[],
  capacity:{cluster:{status:"clear",free_bytes:10485760,total_bytes:20971520},
   bucket:{status:"clear",free_bytes:null,quota_bytes:0,used_bytes:100,age_seconds:1,unlimited:true}}},
 ]});
 render(<HealthSignals value={value} onOperation={()=>{}}/>);
 expect(screen.getByText(/未设置桶配额|No bucket quota|バケットの容量制限なし/)).toBeInTheDocument();
});

it("separates recorded authentication and stream failures from the cross-account alert",()=>{
 const value=healthReport.parse({checked_at:"2026-09-28T00:00:00Z",stalled_seconds:300,recent_seconds:3600,signals:[
 {code:"access_denials",status:"clear",severity:"critical",count:0,threshold:10,operation_ids:[],
  coverage:{scope:"private_resource_rejections",status:"complete",active_processes:1,expected_processes:1,
   stale_processes:0,gap_recorded:false,inventory_complete:true},
  categories:{cross:0,miss:2,unauthenticated:12,forbidden:3,rejected:1,stream_closed:4}},
 ]});
 render(<HealthSignals value={value} onOperation={()=>{}}/>);
 expect(screen.getByText(/旧私有资源|private legacy resources|旧非公開リソース/)).toBeInTheDocument();
 const counts=screen.getByText(/已记录：|Recorded:|記録済み：/);
 for(const count of ["12","3","4"])expect(counts).toHaveTextContent(count);
 expect(value.signals[0].status).toBe("clear");
});
