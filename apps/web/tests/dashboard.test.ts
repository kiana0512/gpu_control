import { mount } from "@vue/test-utils";
import StatusMark from "../src/components/StatusMark.vue";

describe("StatusMark", () => {
  it("renders state text and semantic class", () => {
    const wrapper = mount(StatusMark, { props: { value: "RESERVED" } });
    expect(wrapper.text()).toContain("RESERVED");
    expect(wrapper.classes()).toContain("reserved");
  });
});
