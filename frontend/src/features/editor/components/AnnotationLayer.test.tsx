import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AnnotationLayer } from "./AnnotationLayer";
import type { AnnotationMark } from "../deformation/recipe";
afterEach(cleanup);
const mark: AnnotationMark = {id:"text",kind:"text",x:.5,y:.5,endX:.5,endY:.5,width:.2,height:.1,note:" KEEP\nTHIS "};
describe("annotation text image coordinates",()=>{
  it("uses export text fallback, font and one-line layout at the image scale",()=>{
    render(<AnnotationLayer annotations={[mark]} width={720} height={720} imagePixelScale={.5}/>);
    const text=screen.getByTestId("annotation-text-1");
    expect(text).toHaveTextContent("KEEP THIS");
    expect(text).toHaveStyle({fontSize:"11px",fontFamily:"sans-serif",fontWeight:700,whiteSpace:"pre",textAlign:"center"});
  });
  it("changes only preview scale while preserving explicit font size and annotation content",()=>{
    const annotation={...mark,text:"CHECK",fontSize:32};
    const {rerender}=render(<AnnotationLayer annotations={[annotation]} width={720} height={720} imagePixelScale={1}/>);
    expect(screen.getByTestId("annotation-text-1")).toHaveStyle({fontSize:"32px"});
    rerender(<AnnotationLayer annotations={[annotation]} width={720} height={720} imagePixelScale={.25}/>);
    expect(screen.getByTestId("annotation-text-1")).toHaveStyle({fontSize:"8px"});
    expect(screen.getByTestId("annotation-text-1")).toHaveTextContent("CHECK");
    expect(annotation.fontSize).toBe(32);
  });
});
