# bsp-vrad-merging
# EXPERIMENTAL
**BSP VRAD Merging** is a tool for Source Engine games that transfers baked static shadows from one map to another.

The main goal is to add **highly detailed static shadows** from complex geometry without the performance and memory cost of including the original shadow-casting geometry in the destination map.

Instead of copying the actual brushes or models that produce the shadows, the tool transfers **only their baked lighting data**. This allows the destination map to retain detailed shadows while avoiding the need to keep the geometry responsible for casting them.


## Usage

1. Fully compile the **source BSP** containing the expensive geometry. This
   generates the baked static shadows that will be transferred.

2. Replace the expensive geometry in the source BSP with a cheaper,
   simplified version. Alternatively, remove the geometry entirely if that
   better suits your use case.

3. Fully compile the **target BSP** containing the cheaper geometry.

4. Run BSP VRAD Merging:
  ```
  python bsp_vradmerging.py --source expensive.bsp --target cheap.bsp --out output.bsp
  ```


  ## Notes

- You do **not** need to use the entire BSP as the source. You can remove
  everything except the location containing the expensive geometry whose shadows
  you want to transfer and the surfaces those shadows are cast onto. Compile
  that BSP and use it as the source file.

- Keep in mind that the coordinates of the faces receiving the shadows must
  match between the source and target BSPs. The tool relies on the geometry
  being in the corresponding locations.

- This tool is still **experimental** and has not been tested against a large
  variety of BSPs. There may be bugs, unexpected behavior, or unusual edge
  cases that are not currently handled.
