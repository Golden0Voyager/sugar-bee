/**
     * 选择分析选项并触发分析
     */
    async function selectAnalysisOption(days) {
        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('analysisOptionsModal'));
        modal.hide();

        // 显示加载提示
        showToast('正在生成分析报告...', 'info');

        // 触发分析
        await triggerManualAnalysis(days);
    }

    /**
     * 显示 Toast 提示
     */
    // showToast 已在上方统一定义
