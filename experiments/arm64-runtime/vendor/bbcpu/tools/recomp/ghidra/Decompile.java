// tools/recomp/ghidra/Decompile.java OUTDIR OFFSET...: Ghidra's C for functions of the game image, one
// file per function (OUTDIR/<offset>.c, its instructions in OUTDIR/<offset>.s), without analyzing
// the whole image (docs/RECOMPILATION.md).
// Offsets are image offsets (tools/recomp/scan.c); each function is disassembled and created where
// the unwind tables put it. Run through tools/recomp/inspect.sh.
// @category bbport

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;

public class Decompile extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        File out = new File(args[0]);
        out.mkdirs();
        DecompInterface decompiler = new DecompInterface();
        decompiler.setOptions(new DecompileOptions());
        decompiler.openProgram(currentProgram);
        Address base = currentProgram.getImageBase();
        for (int i = 1; i < args.length; ++i) {
            long offset = Long.decode(args[i]);
            Address address = base.add(offset);
            new DisassembleCommand(address, null, true).applyTo(currentProgram, monitor);
            Function function = getFunctionAt(address);
            if (function == null) {
                new CreateFunctionCmd(address).applyTo(currentProgram, monitor);
                function = getFunctionAt(address);
            }
            String name = String.format("0x%x", offset);
            if (function == null) {
                println(name + ": no function");
                continue;
            }
            DecompileResults result = decompiler.decompileFunction(function, 120, monitor);
            try (PrintWriter writer = new PrintWriter(new File(out, name + ".c"))) {
                if (result.decompileCompleted()) {
                    writer.println(result.getDecompiledFunction().getC());
                } else {
                    writer.println("// decompilation failed: " + result.getErrorMessage());
                }
            }
            // The instructions too (image offsets): what the C must do exactly (floating point).
            try (PrintWriter writer = new PrintWriter(new File(out, name + ".s"))) {
                for (Instruction in : currentProgram.getListing().getInstructions(function.getBody(), true)) {
                    writer.printf("%8x  %s%n", in.getAddress().subtract(base), in.toString());
                }
            }
            println(name + ": " + (result.decompileCompleted() ? "ok" : result.getErrorMessage()));
        }
        decompiler.dispose();
    }
}
