import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ParameterSlider } from "../components/ParameterSlider";
import { EditorInputFlushContext } from "./inputFlush";

describe("editor input flush", () => {
  it("exports the last queued slider value synchronously and only flushes its own editor", () => {
    const first = new Set<() => void>(), second = new Set<() => void>();
    const onFirst = vi.fn(), onSecond = vi.fn(), committed = vi.fn();
    const props = { defaultValue:0,max:1,min:-1,step:.1,value:0,onReset:vi.fn() };
    render(<>
      <EditorInputFlushContext.Provider value={first}>
        <ParameterSlider {...props} dataTestId="first" label="First" onChange={onFirst} onInteractionEnd={committed}/>
      </EditorInputFlushContext.Provider>
      <EditorInputFlushContext.Provider value={second}>
        <ParameterSlider {...props} dataTestId="second" label="Second" onChange={onSecond}/>
      </EditorInputFlushContext.Provider>
    </>);
    fireEvent.input(screen.getByRole("slider",{name:"First"}),{target:{value:"0.5"}});
    fireEvent.input(screen.getByRole("slider",{name:"Second"}),{target:{value:"0.8"}});
    for(const flush of first) flush();
    expect(onFirst).toHaveBeenLastCalledWith(.5);
    expect(committed).toHaveBeenCalledTimes(1);
    expect(onSecond).not.toHaveBeenCalled();
    for(const flush of second) flush();
    expect(onSecond).toHaveBeenLastCalledWith(.8);
  });

  it("flushes rather than discards a queued value when its parameter panel unmounts", () => {
    const onChange = vi.fn();
    const view = render(<ParameterSlider dataTestId="changing" label="Changing" defaultValue={0}
      max={1} min={-1} step={.1} value={0} onChange={onChange} onReset={vi.fn()}/>);
    fireEvent.input(screen.getByRole("slider",{name:"Changing"}),{target:{value:".7"}});
    view.unmount();
    expect(onChange).toHaveBeenCalledWith(.7);
  });
});
