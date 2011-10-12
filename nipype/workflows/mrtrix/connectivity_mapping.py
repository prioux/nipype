import nipype.interfaces.io as nio           # Data i/o
import nipype.interfaces.utility as util     # utility
import nipype.pipeline.engine as pe          # pypeline engine
import nipype.interfaces.fsl as fsl
import nipype.interfaces.freesurfer as fs    # freesurfer
import nipype.interfaces.mrtrix as mrtrix
import nipype.interfaces.cmtk as cmtk
import inspect
import nibabel as nb
import os                                    # system functions
import cmp                                    # connectome mapper
from nipype.workflows.camino.connectivity_mapping import (get_vox_dims, get_data_dims,
 get_affine, select_aparc, select_aparc_annot, get_first_image)
 
def create_connectivity_pipeline(name="connectivity"):
	"""Creates a pipeline that does the same connectivity processing as in the
	connectivity_tutorial example script. Given a subject id (and completed Freesurfer reconstruction)
	diffusion-weighted image, b-values, and b-vectors, the workflow will return the subject's connectome
	as a Connectome File Format (CFF) file for use in Connectome Viewer (http://www.cmtk.org).

	Example
	-------

	>>> import os
	>>> import nipype.interfaces.freesurfer as fs
	>>> import nipype.workflows.camino as cmonwk
	>>> subjects_dir = os.path.abspath('freesurfer')
	>>> fs.FSCommand.set_default_subjects_dir(subjects_dir)
	>>> conmapper = cmonwk.create_connectivity_pipeline("nipype_conmap")
	>>> conmapper.inputs.inputnode.subjects_dir = subjects_dir
	>>> conmapper.inputs.inputnode.subject_id = 'subj1'
	>>> conmapper.inputs.inputnode.dwi = os.path.abspath('fsl_course_data/fdt/subj1/data.nii.gz')
	>>> conmapper.inputs.inputnode.bvecs = os.path.abspath('fsl_course_data/fdt/subj1/bvecs')
	>>> conmapper.inputs.inputnode.bvals = os.path.abspath('fsl_course_data/fdt/subj1/bvals')
	>>> conmapper.run()                 # doctest: +SKIP

	Inputs::

		inputnode.subject_id
		inputnode.subjects_dir
		inputnode.dwi
		inputnode.bvecs
		inputnode.bvals

	Outputs::

		outputnode.connectome
		outputnode.cmatrix
		outputnode.gpickled_network
		outputnode.fa
		outputnode.struct
		outputnode.trace
		outputnode.tracts
		outputnode.tensors

	"""

	inputnode1 = pe.Node(interface=util.IdentityInterface(fields=["subject_id","dwi", "bvecs", "bvals", "subjects_dir"]), name="inputnode1")

	FreeSurferSource = pe.Node(interface=nio.FreeSurferSource(), name='fssource')
	FreeSurferSourceLH = pe.Node(interface=nio.FreeSurferSource(), name='fssourceLH')
	FreeSurferSourceLH.inputs.hemi = 'lh'

	FreeSurferSourceRH = pe.Node(interface=nio.FreeSurferSource(), name='fssourceRH')
	FreeSurferSourceRH.inputs.hemi = 'rh'

	"""
	Use FSL's Brain Extraction to create a mask from the b0 image
	"""
	bet = pe.Node(interface=fsl.BET(mask = True), name = 'bet_b0')

	"""
	A number of conversion operations are required to obtain NIFTI files from the FreesurferSource for each subject.
	Nodes are used to convert the following:
		* Original structural image to NIFTI
		* Parcellated white matter image to NIFTI
		* Parcellated whole-brain image to NIFTI
		* Pial, white, inflated, and spherical surfaces for both the left and right hemispheres
			are converted to GIFTI for visualization in ConnectomeViewer
		* Parcellated annotation files for the left and right hemispheres are also converted to GIFTI
	"""

	mri_convert_Brain = pe.Node(interface=fs.MRIConvert(), name='mri_convert_Brain')
	mri_convert_Brain.inputs.out_type = 'nii'

	mri_convert_WMParc = mri_convert_Brain.clone('mri_convert_WMParc')
	mri_convert_AparcAseg = mri_convert_Brain.clone('mri_convert_AparcAseg')

	mris_convertLH = pe.Node(interface=fs.MRIsConvert(), name='mris_convertLH')
	mris_convertLH.inputs.out_datatype = 'gii'
	mris_convertRH = mris_convertLH.clone('mris_convertRH')
	mris_convertRHwhite = mris_convertLH.clone('mris_convertRHwhite')
	mris_convertLHwhite = mris_convertLH.clone('mris_convertLHwhite')
	mris_convertRHinflated = mris_convertLH.clone('mris_convertRHinflated')
	mris_convertLHinflated = mris_convertLH.clone('mris_convertLHinflated')
	mris_convertRHsphere = mris_convertLH.clone('mris_convertRHsphere')
	mris_convertLHsphere = mris_convertLH.clone('mris_convertLHsphere')
	mris_convertLHlabels = mris_convertLH.clone('mris_convertLHlabels')
	mris_convertRHlabels = mris_convertLH.clone('mris_convertRHlabels')
	
	dwi2tensor = pe.Node(interface=mrtrix.DWI2Tensor(),name='dwi2tensor')
	fsl2mrtrix = pe.Node(interface=mrtrix.FSL2MRTrix(),name='fsl2mrtrix')

	binarizeWMparc = pe.Node(interface=fsl.UnaryMaths(),name='binarizeWMparc')
	binarizeWMparc.inputs.operation = 'bin'

	tensor2vector = pe.Node(interface=mrtrix.Tensor2Vector(),name='tensor2vector')
	tensor2adc = pe.Node(interface=mrtrix.Tensor2ApparentDiffusion(),name='tensor2adc')
	tensor2fa = pe.Node(interface=mrtrix.Tensor2FractionalAnisotropy(),name='tensor2fa')

	erode_mask_firstpass = pe.Node(interface=mrtrix.Erode(),name='erode_mask_firstpass')
	erode_mask_secondpass = pe.Node(interface=mrtrix.Erode(),name='erode_mask_secondpass')

	threshold_b0 = pe.Node(interface=mrtrix.Threshold(),name='threshold_b0')

	threshold_FA = pe.Node(interface=mrtrix.Threshold(),name='threshold_FA')
	threshold_FA.inputs.absolute_threshold_value = 0.7

	threshold_wmmask = pe.Node(interface=mrtrix.Threshold(),name='threshold_wmmask')
	threshold_wmmask.inputs.absolute_threshold_value = 0.4

	MRmultiply = pe.Node(interface=mrtrix.MRMultiply(),name='MRmultiply')
	median3d = pe.Node(interface=mrtrix.MedianFilter3D(),name='median3d')

	MRconvert = pe.Node(interface=mrtrix.MRConvert(),name='MRconvert')
	MRconvert.inputs.extract_at_axis = 3
	MRconvert.inputs.extract_at_coordinate = [0]

	MRmult_merge = pe.Node(interface=util.Merge(2), name="MRmultiply_merge")

	csdeconv = pe.Node(interface=mrtrix.ConstrainedSphericalDeconvolution(),name='csdeconv')
	csdeconv.inputs.maximum_harmonic_order = 6


	estimateresponse = pe.Node(interface=mrtrix.EstimateResponseForSH(),name='estimateresponse')
	estimateresponse.inputs.maximum_harmonic_order = 6

	gen_WM_mask = pe.Node(interface=mrtrix.GenerateWhiteMatterMask(),name='gen_WM_mask')    


	probCSDstreamtrack = pe.Node(interface=mrtrix.ProbabilisticSphericallyDeconvolutedStreamlineTrack(),name='probCSDstreamtrack')
	probCSDstreamtrack.inputs.inputmodel = 'SD_PROB'
	probCSDstreamtrack.inputs.maximum_number_of_tracks = 150000
	probSHstreamtrack = probCSDstreamtrack.clone(name="probSHstreamtrack")

	tracks2prob = pe.Node(interface=mrtrix.Tracks2Prob(),name='tracks2prob')
	tracks2prob.inputs.colour = True

	tck2trk = pe.Node(interface=mrtrix.MRTrix2TrackVis(),name='tck2trk')




	MRconvert_vector = MRconvert.clone(name="MRconvert_vector")
	MRconvert_ADC = MRconvert.clone(name="MRconvert_ADC")
	MRconvert_FA = MRconvert.clone(name="MRconvert_FA")
	MRconvert_TDI = MRconvert.clone(name="MRconvert_TDI")
	parcellate = pe.Node(interface=cmtk.Parcellate(), name="Parcellate")
	parcellation_name = 'scale500'
	parcellate.inputs.parcellation_name = parcellation_name

	"""
	The CreateMatrix interface takes in the remapped aparc+aseg image as well as the label dictionary and fiber tracts
	and outputs a number of different files. The most important of which is the connectivity network itself, which is stored
	as a 'gpickle' and can be loaded using Python's NetworkX package (see CreateMatrix docstring). Also outputted are various
	NumPy arrays containing detailed tract information, such as the start and endpoint regions, and statistics on the mean and
	standard deviation for the fiber length of each connection. These matrices can be used in the ConnectomeViewer to plot the
	specific tracts that connect between user-selected regions.
	"""

	creatematrix = pe.Node(interface=cmtk.CreateMatrix(), name="CreateMatrix")
	cmp_config = cmp.configuration.PipelineConfiguration()
	cmp_config.parcellation_scheme = "Lausanne2008"
	creatematrix.inputs.resolution_network_file = cmp_config._get_lausanne_parcellation('Lausanne2008')[parcellation_name]['node_information_graphml']
	ntwkMetrics = pe.Node(interface=cmtk.NetworkXMetrics(), name="NetworkXMetrics")


	"""
	Here we define the endpoint of this tutorial, which is the CFFConverter node, as well as a few nodes which use
	the Nipype Merge utility. These are useful for passing lists of the files we want packaged in our CFF file.
	"""

	CFFConverter = pe.Node(interface=cmtk.CFFConverter(), name="CFFConverter")
	NxStatsCFFConverter = pe.Node(interface=cmtk.CFFConverter(), name="NxStatsCFFConverter")


	giftiSurfaces = pe.Node(interface=util.Merge(8), name="GiftiSurfaces")
	giftiLabels = pe.Node(interface=util.Merge(2), name="GiftiLabels")
	niftiVolumes = pe.Node(interface=util.Merge(3), name="NiftiVolumes")
	fiberDataArrays = pe.Node(interface=util.Merge(4), name="FiberDataArrays")
	gpickledNetworks = pe.Node(interface=util.Merge(2), name="NetworkFiles")

	"""
	Since we have now created all our nodes, we can define our workflow and start making connections.
	"""

	resampleb0 = pe.Node(interface=fs.MRIConvert(), name='resampleb0')
	resampleb0.inputs.out_type = 'nii'
	resampleb0.inputs.vox_size = (1, 1, 1)
	coregister = pe.Node(interface=fsl.FLIRT(dof=6), name = 'coregister')
	coregister.inputs.cost = ('normmi')

	convertxfm = pe.Node(interface=fsl.ConvertXFM(), name = 'convertxfm')
	convertxfm.inputs.invert_xfm = True

	inverse = pe.Node(interface=fsl.FLIRT(), name = 'inverse')
	inverse.inputs.interp = ('nearestneighbour')
	inverse.inputs.apply_xfm = True

	inverse_AparcAseg = pe.Node(interface=fsl.FLIRT(), name = 'inverse_AparcAseg')
	inverse_AparcAseg.inputs.interp = ('nearestneighbour')
	inverse_AparcAseg.inputs.apply_xfm = True
	CFFConverter.inputs.script_files = os.path.abspath(inspect.getfile(inspect.currentframe()))
	NxStatsCFFConverter.inputs.script_files = os.path.abspath(inspect.getfile(inspect.currentframe()))

	"""
	Connecting the mapping workflow
	--------------------------------------
	Here we connect our processing pipeline.
	"""

	mapping = pe.Workflow(name='mapping')

	"""
	First, we connect the input node to the early conversion functions.
	FreeSurfer input nodes:
	"""


	mapping.connect([(inputnode1, FreeSurferSource,[("subjects_dir","subjects_dir")])])
	mapping.connect([(inputnode1, FreeSurferSource,[("subject_id","subject_id")])])

	mapping.connect([(inputnode1, FreeSurferSourceLH,[("subjects_dir","subjects_dir")])])
	mapping.connect([(inputnode1, FreeSurferSourceLH,[("subject_id","subject_id")])])

	mapping.connect([(inputnode1, FreeSurferSourceRH,[("subjects_dir","subjects_dir")])])
	mapping.connect([(inputnode1, FreeSurferSourceRH,[("subject_id","subject_id")])])

	mapping.connect([(inputnode1, parcellate,[("subjects_dir","subjects_dir")])])

	"""
	Nifti conversions for the parcellated white matter image (used in Camino's conmap),
	and the subject's stripped brain image from Freesurfer:
	"""

	mapping.connect([(FreeSurferSource, mri_convert_WMParc,[('wmparc','in_file')])])
	mapping.connect([(FreeSurferSource, mri_convert_Brain,[('brain','in_file')])])

	"""
	Surface conversions to GIFTI (pial, white, inflated, and sphere for both hemispheres)
	"""

	mapping.connect([(FreeSurferSourceLH, mris_convertLH,[('pial','in_file')])])
	mapping.connect([(FreeSurferSourceRH, mris_convertRH,[('pial','in_file')])])
	mapping.connect([(FreeSurferSourceLH, mris_convertLHwhite,[('white','in_file')])])
	mapping.connect([(FreeSurferSourceRH, mris_convertRHwhite,[('white','in_file')])])
	mapping.connect([(FreeSurferSourceLH, mris_convertLHinflated,[('inflated','in_file')])])
	mapping.connect([(FreeSurferSourceRH, mris_convertRHinflated,[('inflated','in_file')])])
	mapping.connect([(FreeSurferSourceLH, mris_convertLHsphere,[('sphere','in_file')])])
	mapping.connect([(FreeSurferSourceRH, mris_convertRHsphere,[('sphere','in_file')])])

	"""
	The annotation files are converted using the pial surface as a map via the MRIsConvert interface.
	One of the functions defined earlier is used to select the lh.aparc.annot and rh.aparc.annot files
	specifically (rather than i.e. rh.aparc.a2009s.annot) from the output list given by the FreeSurferSource.
	"""

	mapping.connect([(FreeSurferSourceLH, mris_convertLHlabels,[('pial','in_file')])])
	mapping.connect([(FreeSurferSourceRH, mris_convertRHlabels,[('pial','in_file')])])
	mapping.connect([(FreeSurferSourceLH, mris_convertLHlabels, [(('annot', select_aparc_annot), 'annot_file')])])
	mapping.connect([(FreeSurferSourceRH, mris_convertRHlabels, [(('annot', select_aparc_annot), 'annot_file')])])
													
	mapping.connect([(inputnode1, fsl2mrtrix, [("bvecs", "bvec_file"),
													("bvals", "bval_file")])])
	mapping.connect([(inputnode1, dwi2tensor,[("dwi","in_file")])])
	mapping.connect([(fsl2mrtrix, dwi2tensor,[("encoding_file","encoding_file")])])

	mapping.connect([(dwi2tensor, tensor2vector,[['tensor','in_file']]),
						   (dwi2tensor, tensor2adc,[['tensor','in_file']]),
						   (dwi2tensor, tensor2fa,[['tensor','in_file']]),
						  ])
	mapping.connect([(inputnode1, MRconvert,[("dwi","in_file")])])
	mapping.connect([(MRconvert, threshold_b0,[("converted","in_file")])])
	mapping.connect([(threshold_b0, median3d,[("out_file","in_file")])])
	mapping.connect([(median3d, erode_mask_firstpass,[("out_file","in_file")])])
	mapping.connect([(erode_mask_firstpass, erode_mask_secondpass,[("out_file","in_file")])])

	mapping.connect([(tensor2fa, MRmult_merge,[("FA","in1")])])
	mapping.connect([(erode_mask_secondpass, MRmult_merge,[("out_file","in2")])])
	mapping.connect([(MRmult_merge, MRmultiply,[("out","in_files")])])
	mapping.connect([(MRmultiply, threshold_FA,[("out_file","in_file")])])
	mapping.connect([(threshold_FA, estimateresponse,[("out_file","mask_image")])])

	mapping.connect([(inputnode1, bet,[("dwi","in_file")])])
	mapping.connect([(inputnode1, gen_WM_mask,[("dwi","in_file")])])
	mapping.connect([(bet, gen_WM_mask,[("mask_file","binary_mask")])])
	mapping.connect([(fsl2mrtrix, gen_WM_mask,[("encoding_file","encoding_file")])])

	mapping.connect([(inputnode1, estimateresponse,[("dwi","in_file")])])
	mapping.connect([(fsl2mrtrix, estimateresponse,[("encoding_file","encoding_file")])])

	mapping.connect([(inputnode1, csdeconv,[("dwi","in_file")])])
	mapping.connect([(gen_WM_mask, csdeconv,[("WMprobabilitymap","mask_image")])])
	mapping.connect([(estimateresponse, csdeconv,[("response","response_file")])])
	mapping.connect([(fsl2mrtrix, csdeconv,[("encoding_file","encoding_file")])])

	mapping.connect([(gen_WM_mask, threshold_wmmask,[("WMprobabilitymap","in_file")])])
	mapping.connect([(threshold_wmmask, probCSDstreamtrack,[("out_file","seed_file")])])
	mapping.connect([(csdeconv, probCSDstreamtrack,[("spherical_harmonics_image","in_file")])])
	mapping.connect([(probCSDstreamtrack, tracks2prob,[("tracked","in_file")])])
	mapping.connect([(inputnode1, tracks2prob,[("dwi","template_file")])])
	
	mapping.connect([(inputnode1, coregister,[('dwi','in_file')])])
	mapping.connect([(mri_convert_Brain, coregister,[('out_file','reference')])])
	mapping.connect([(coregister, convertxfm,[('out_matrix_file','in_file')])])
	mapping.connect([(inputnode1, inverse,[('dwi','reference')])])

	mapping.connect([(convertxfm, inverse,[('out_file','in_matrix_file')])])
	mapping.connect([(mri_convert_Brain, inverse,[('out_file','in_file')])])

	mapping.connect([(inputnode1, resampleb0,[(('dwi', get_first_image), 'in_file')])])
	mapping.connect([(resampleb0, inverse_AparcAseg,[('out_file','reference')])])
	mapping.connect([(convertxfm, inverse_AparcAseg,[('out_file','in_matrix_file')])])
	mapping.connect([(parcellate, inverse_AparcAseg,[('roi_file','in_file')])])

	mapping.connect([(inputnode1, creatematrix,[("subject_id","out_matrix_file")])])
	mapping.connect([(inputnode1, creatematrix,[("subject_id","out_matrix_mat_file")])])

	mapping.connect([(inputnode1, parcellate,[("subject_id","subject_id")])])
	mapping.connect([(inverse_AparcAseg, creatematrix,[("out_file","roi_file")])])

	mapping.connect([(probCSDstreamtrack, tck2trk,[("tracked","in_file")])])
	mapping.connect([(tck2trk, creatematrix,[("out_file","tract_file")])])
	mapping.connect([(inputnode1, tck2trk,[("dwi","image_file")])])

	"""
	The merge nodes defined earlier are used here to create lists of the files which are
	destined for the CFFConverter.
	"""

	mapping.connect([(mris_convertLH, giftiSurfaces,[("converted","in1")])])
	mapping.connect([(mris_convertRH, giftiSurfaces,[("converted","in2")])])
	mapping.connect([(mris_convertLHwhite, giftiSurfaces,[("converted","in3")])])
	mapping.connect([(mris_convertRHwhite, giftiSurfaces,[("converted","in4")])])
	mapping.connect([(mris_convertLHinflated, giftiSurfaces,[("converted","in5")])])
	mapping.connect([(mris_convertRHinflated, giftiSurfaces,[("converted","in6")])])
	mapping.connect([(mris_convertLHsphere, giftiSurfaces,[("converted","in7")])])
	mapping.connect([(mris_convertRHsphere, giftiSurfaces,[("converted","in8")])])

	mapping.connect([(mris_convertLHlabels, giftiLabels,[("converted","in1")])])
	mapping.connect([(mris_convertRHlabels, giftiLabels,[("converted","in2")])])

	mapping.connect([(inputnode1, niftiVolumes,[("dwi","in2")])])
	mapping.connect([(mri_convert_Brain, niftiVolumes,[("out_file","in3")])])

	mapping.connect([(creatematrix, fiberDataArrays,[("endpoint_file","in1")])])
	mapping.connect([(creatematrix, fiberDataArrays,[("endpoint_file_mm","in2")])])
	mapping.connect([(creatematrix, fiberDataArrays,[("fiber_length_file","in3")])])
	mapping.connect([(creatematrix, fiberDataArrays,[("fiber_label_file","in4")])])

	mapping.connect([(creatematrix, ntwkMetrics,[("matrix_file","in_file")])])
	mapping.connect([(creatematrix, gpickledNetworks,[("matrix_file","in1")])])

	"""
	This block actually connects the merged lists to the CFF converter. We pass the surfaces
	and volumes that are to be included, as well as the tracts and the network itself. The currently
	running pipeline (connectivity_tutorial.py) is also scraped and included in the CFF file. This
	makes it easy for the user to examine the entire processing pathway used to generate the end
	product.
	"""

	mapping.connect([(giftiSurfaces, CFFConverter,[("out","gifti_surfaces")])])
	mapping.connect([(giftiLabels, CFFConverter,[("out","gifti_labels")])])
	mapping.connect([(creatematrix, CFFConverter,[("matrix_file","gpickled_networks")])])    
	mapping.connect([(niftiVolumes, CFFConverter,[("out","nifti_volumes")])])
	mapping.connect([(fiberDataArrays, CFFConverter,[("out","data_files")])])
	mapping.connect([(inputnode1, CFFConverter,[("subject_id","title")])])

	mapping.connect([(ntwkMetrics, gpickledNetworks,[("gpickled_network_files","in2")])])
	mapping.connect([(giftiSurfaces, NxStatsCFFConverter,[("out","gifti_surfaces")])])
	mapping.connect([(giftiLabels, NxStatsCFFConverter,[("out","gifti_labels")])])
	mapping.connect([(gpickledNetworks, NxStatsCFFConverter,[("out","gpickled_networks")])])    
	mapping.connect([(niftiVolumes, NxStatsCFFConverter,[("out","nifti_volumes")])])
	mapping.connect([(fiberDataArrays, NxStatsCFFConverter,[("out","data_files")])])
	mapping.connect([(inputnode1, NxStatsCFFConverter,[("subject_id","title")])])

	"""
	Create a higher-level workflow
	--------------------------------------
	Finally, we create another higher-level workflow to connect our mapping workflow with the info and datagrabbing nodes
	declared at the beginning. Our tutorial can is now extensible to any arbitrary number of subjects by simply adding
	their names to the subject list and their data to the proper folders.
	"""

	#fsl2mrtrix.overwrite = True
	#ntwkMetrics.overwrite = True
	#csdeconv.overwrite = True
	#probCSDstreamtrack.overwrite = True
	#tck2trk.overwrite = True
	#inverse_AparcAseg.overwrite = True
	#creatematrix.overwrite = True
	#CFFConverter.overwrite = True
	inputnode = pe.Node(interface=util.IdentityInterface(fields=["subject_id", "dwi", "bvecs", "bvals", "subjects_dir"]), name="inputnode")

	outputnode = pe.Node(interface = util.IdentityInterface(fields=["fa",
																"struct",
																"tracts",
																"connectome",
																"nxstatscff",
																"cmatrix",
																"gpickled_network",
																"rois",
																"rois_orig",
                                                                "odfs",
																"warped",
																"mean_fiber_length",
																"fiber_length_std"]),
										name="outputnode")

	connectivity = pe.Workflow(name="connectivity")
	connectivity.base_output_dir=name
	connectivity.base_dir=name

	connectivity.connect([(inputnode, mapping, [("dwi", "inputnode1.dwi"),
											  ("bvals", "inputnode1.bvals"),
											  ("bvecs", "inputnode1.bvecs"),
											  ("subject_id", "inputnode1.subject_id"),
											  ("subjects_dir", "inputnode1.subjects_dir")])
											  ])

	connectivity.connect([(mapping, outputnode, [("tck2trk.out_file", "tracts"),
		("CFFConverter.connectome_file", "connectome"),
		("NxStatsCFFConverter.connectome_file", "nxstatscff"),
		("CreateMatrix.matrix_mat_file", "cmatrix"),
		("CreateMatrix.mean_fiber_length_matrix_mat_file", "mean_fiber_length"),
		("CreateMatrix.fiber_length_std_matrix_mat_file", "fiber_length_std"),
		("CreateMatrix.matrix_file", "gpickled_network"),
		("inverse_AparcAseg.out_file", "rois_orig"),
		("Parcellate.roi_file", "rois"),
		("tensor2fa.FA", "fa"),
		("inverse_AparcAseg.out_file", "warped"),
		("mri_convert_Brain.out_file", "struct")])
		])

	return connectivity
