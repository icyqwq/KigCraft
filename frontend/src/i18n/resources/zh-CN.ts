const zhCN = {
  common: {
    language: "语言",
    refresh: "刷新",
    back: "返回",
    delete: "删除",
    replace: "替换",
  },
  workflow: {
    nav: {
      workbench: "生成工作台",
      editor: "编辑器",
      history: "任务记录",
      about: "关于",
      menu: "菜单",
    },
    steps: {
      upload: "上传参考图",
      detail: "确认细节",
      front: "正视图",
      turnaround: "四视图",
    },
    actions: {
      confirmDetails: "确认细节",
      generateFrontDirect: "直接生成",
      generateFront: "生成正视图",
    },
  },
  detailConfirmation: {
    title: "确认细节",
    subtitle: "检查需要锁定的角色特征。",
    reanalyze: "重新分析",
    backToUpload: "返回上传",
    generateFront: "生成正视图",
    featureListTitle: "角色特征",
    cropListTitle: "细节参考",
    addFeature: "新增文字项",
    addCrop: "新增细节图片",
    manualKindLabel: "细节类型",
    manualDescriptionLabel: "细节说明",
    manualImageLabel: "选择图片",
    saveManualFeature: "保存文字项",
    saveManualCrop: "保存图片",
    cancelManual: "取消",
    noFeatures: "暂无需要确认的特征。",
    noCrops: "暂无细节切片。",
    featurePlaceholder: "补充这项特征",
    cropPlaceholder: "描述这张切片",
    featureInputAria: "{{kind}}特征",
    cropInputAria: "{{kind}}切片",
    deleteFeature: "删除 {{description}}",
    deleteFeatureAria: "删除 {{description}}",
    deleteCrop: "删除 {{description}}",
    deleteCropAria: "删除 {{description}}",
    replaceCropAria: "替换 {{description}}",
    analysisStatus: {
      title: "分析细节中",
      subtitle: "正在整理角色特征和细节切片",
      elapsed: "已用时间 {{duration}}",
      eta: "预计剩余 {{duration}}",
    },
    kind: {
      hair: "发型",
      eyes: "眼睛",
      expression: "表情",
      headwear: "头饰",
      accessory: "配件",
      ears: "耳朵",
      requirement: "要求",
      outfit: "服装",
      color: "颜色",
      avoid: "避免",
      other: "其他",
    },
  },
  generation: {
    statusTitle: "生成状态",
    waitingTitle: "等待生成",
    waitingDescription: "提交后这里会显示排队和生成进度。",
    waitingForProgress: "等待生成进度更新",
    jobLabel: "任务 {{id}}",
    progressAria: "生成进度",
    elapsed: "已用时间 {{duration}}",
    eta: "预计剩余 {{duration}}",
    queuePosition: "排队序号 {{position}}",
  },
} as const;

export type TranslationResource = {
  [Key in keyof typeof zhCN]: WidenTranslationStrings<(typeof zhCN)[Key]>;
};

type WidenTranslationStrings<Value> = Value extends string
  ? string
  : {
      [Key in keyof Value]: WidenTranslationStrings<Value[Key]>;
    };

export default zhCN;
